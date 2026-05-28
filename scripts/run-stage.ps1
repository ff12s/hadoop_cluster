#Requires -Version 5.1
param(
    [Parameter(Mandatory=$true)][string]$Label,
    [Parameter(Mandatory=$true)][string]$LogFile,
    [Parameter(Mandatory=$true)][string]$Command,
    [switch]$NoTailOnFail
)

$ErrorActionPreference = 'Stop'

# Docker output is UTF-8. Without this, the log reader would interpret bytes as
# system ANSI (CP-1251 on Russian Windows), so any non-ASCII char from a script
# like check-hdfs.sh's "⚠ No live DataNodes..." comes through as mojibake.
# Setting OutputEncoding also lets the █/░ bar glyphs render on terminals that
# support them; legacy consoles fall back to "?", still readable.
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}

# Bar glyphs built from explicit Unicode codepoints so the script source itself
# stays ASCII-safe (PowerShell 5.1 reads BOM-less files as system ANSI, which
# would mangle literal block-drawing characters in the source).
$BAR_FULL  = [string][char]0x2588   # FULL BLOCK
$BAR_EMPTY = [string][char]0x2591   # LIGHT SHADE

function Format-Elapsed([TimeSpan]$ts) {
    $totalSec = [int][Math]::Floor($ts.TotalSeconds)
    if ($totalSec -lt 0) { $totalSec = 0 }
    if ($totalSec -ge 3600) {
        $h = [int]($totalSec / 3600); $m = [int](($totalSec % 3600) / 60)
        return "${h}h${m}m"
    } elseif ($totalSec -ge 60) {
        $m = [int]($totalSec / 60); $s = $totalSec % 60
        return "${m}m${s}s"
    } else {
        return "${totalSec}s"
    }
}

# Reads bytes appended to the log since the last call. Holds the read offset in
# $state.Position so we never re-scan the whole file. Opened with FileShare.ReadWrite
# so cmd's "1>>logfile" appender does not trigger a lock contention.
#
# Reads raw bytes (not via StreamReader) to keep the offset accounting exact -
# StreamReader buffers ahead and leaves $fs.Position past what was actually
# consumed by ReadLine(), which would cause us to skip content next tick.
function Read-NewLines([string]$file, $state) {
    $lines = New-Object System.Collections.Generic.List[string]
    try {
        $fs = [System.IO.File]::Open($file, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, [System.IO.FileShare]::ReadWrite)
    } catch {
        return $lines
    }
    try {
        $currentLength = $fs.Length
        if ($state.Position -gt $currentLength) { $state.Position = 0 }  # log rotated/truncated
        if ($state.Position -eq $currentLength) { return $lines }
        $null = $fs.Seek($state.Position, [System.IO.SeekOrigin]::Begin)
        $bytesToRead = [int]($currentLength - $state.Position)
        $buffer = New-Object byte[] $bytesToRead
        $totalRead = 0
        while ($totalRead -lt $bytesToRead) {
            $r = $fs.Read($buffer, $totalRead, $bytesToRead - $totalRead)
            if ($r -le 0) { break }
            $totalRead += $r
        }

        # Trim back to the last 0x0A so we never decode a partial line. If cmd's
        # >> flush happened mid-line, the trailing fragment stays in the file at
        # our new Position and we pick it up next tick once it is complete.
        # This also keeps any multi-byte UTF-8 sequence whole (a partial sequence
        # at buffer end would decode as U+FFFD; bounding by 0x0A guarantees the
        # boundary always lands on an ASCII byte).
        $lastNewline = -1
        for ($i = $totalRead - 1; $i -ge 0; $i--) {
            if ($buffer[$i] -eq 0x0A) { $lastNewline = $i; break }
        }
        if ($lastNewline -lt 0) {
            # No complete line yet - don't advance Position, try again next tick.
            return $lines
        }
        $consumeLen = $lastNewline + 1
        $text = [System.Text.Encoding]::UTF8.GetString($buffer, 0, $consumeLen)
        foreach ($l in ($text -split "`r?`n")) {
            $lines.Add($l)
        }
        $state.Position += $consumeLen
    } finally {
        $fs.Dispose()
    }
    return $lines
}

# Folds new log lines into accumulated state: BuildKit steps seen/done, docker
# pull layers seen/done, and the most recent "current step" indicator. The state
# hashtable persists across spinner ticks.
function Update-State($state, $lines) {
    foreach ($raw in $lines) {
        if ($null -eq $raw) { continue }
        $line = $raw.Trim()
        if ([string]::IsNullOrEmpty($line)) { continue }
        if ($line -match '^=== STAGE:') { continue }

        # BuildKit step events. Pattern '#N ' or '#N\t' identifies a step header
        # or sub-status line. We don't care which - any reference to step N means
        # BuildKit has dispatched it, so it counts toward the denominator.
        if ($line -match '^#(\d+)(\s|$)') {
            $null = $state.SeenSteps.Add([int]$Matches[1])
        }
        if ($line -match '^#(\d+)\s+(DONE|CACHED)\b') {
            $null = $state.DoneSteps.Add([int]$Matches[1])
        }
        # BuildKit step header with name "[stage X/Y] INSTRUCTION ..." - track
        # the latest non-closing one for the in-flight indicator.
        if ($line -match '^#\d+\s+\[([^\]]+)\]\s*(.*)$') {
            $stepName = $Matches[1]
            $stepDetail = $Matches[2].Trim()
            if ($stepDetail -match '^(DONE|CACHED)') {
                $state.LastClosing = "[$stepName] $stepDetail"
            } else {
                if ($stepDetail) {
                    $state.CurrentStep = "[$stepName] $stepDetail"
                } else {
                    $state.CurrentStep = "[$stepName]"
                }
            }
        }
        # Docker pull layer events.
        if ($line -match '^([0-9a-f]{12,}):\s+Pulling fs layer\s*$') {
            $null = $state.LayersSeen.Add($Matches[1])
        }
        if ($line -match '^([0-9a-f]{12,}):\s+Pull complete\s*$') {
            $null = $state.LayersDone.Add($Matches[1])
        }
        # Per-layer status as current-step hint.
        if ($line -match '^[0-9a-f]{12,}:\s+(.+)$') {
            $state.CurrentStep = $line
        }
        if ($line -match '^Status:\s*(.+)') {
            $state.CurrentStep = $Matches[1]
        }
    }
}

# Returns @{ Progress; Info } for stages that expose parseable progress (build,
# pull), or $null for stages that don't (down/up/health-check shell scripts).
# Progress is clamped monotonically: never regresses, even if BuildKit dispatches
# new steps faster than it completes them and the raw ratio temporarily drops.
function Get-Progress($state) {
    $p = $null
    $info = ""
    if ($state.SeenSteps.Count -gt 0) {
        $p = [double]$state.DoneSteps.Count / $state.SeenSteps.Count
        $info = "$($state.DoneSteps.Count)/$($state.SeenSteps.Count) steps"
    } elseif ($state.LayersSeen.Count -gt 0) {
        $p = [double]$state.LayersDone.Count / $state.LayersSeen.Count
        $info = "$($state.LayersDone.Count)/$($state.LayersSeen.Count) layers"
    }
    if ($null -eq $p) { return $null }
    if ($p -lt $state.MaxProgress) { $p = $state.MaxProgress }
    else { $state.MaxProgress = $p }
    return @{ Progress = $p; Info = $info }
}

function Get-CurrentStepText($state) {
    if ($state.CurrentStep) { return $state.CurrentStep }
    if ($state.LastClosing) { return $state.LastClosing }
    return ""
}

# Start reading from the current EOF of the log so we only see content produced
# by THIS stage. All previous stages already wrote into the same log file - if
# we started at offset 0, we would count their #N DONE lines as part of our
# progress and the bar would start at "99% 30/30 steps" before our command even
# emits its first byte.
#
# Open a fresh FileStream to get an authoritative length: FileInfo.Length (what
# Get-Item returns) is a snapshot of NTFS metadata at the time of the call and
# can lag behind actual disk contents on a recently-written file.
$startPosition = 0
try {
    if (Test-Path $LogFile) {
        $fsInit = [System.IO.File]::Open($LogFile, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, [System.IO.FileShare]::ReadWrite)
        try { $startPosition = $fsInit.Length } finally { $fsInit.Dispose() }
    }
} catch {}

$state = @{
    Position     = $startPosition
    SeenSteps    = New-Object 'System.Collections.Generic.HashSet[int]'
    DoneSteps    = New-Object 'System.Collections.Generic.HashSet[int]'
    LayersSeen   = New-Object 'System.Collections.Generic.HashSet[string]'
    LayersDone   = New-Object 'System.Collections.Generic.HashSet[string]'
    CurrentStep  = ""
    LastClosing  = ""
    MaxProgress  = 0.0
}

$start = Get-Date

# Run via cmd /c with redirect so the child's stdout/stderr stream into the log
# directly, leaving our own stdout free to redraw the bar with `\r`.
$psi = New-Object System.Diagnostics.ProcessStartInfo
$psi.FileName = "cmd.exe"
$psi.Arguments = "/c $Command 1>>`"$LogFile`" 2>&1"
$psi.UseShellExecute = $false
$psi.CreateNoWindow = $true
$proc = [System.Diagnostics.Process]::Start($psi)

$consoleWidth = 80
try {
    $w = $Host.UI.RawUI.WindowSize.Width
    if ($w -gt 0) { $consoleWidth = $w }
} catch {}

# Default to failure - any control-flow path that bypasses the WaitForExit
# below (future refactor, unhandled exception, $proc.Start returning $null
# rather than throwing) surfaces as a stage failure rather than a silent
# "exit 0" that masks the bug.
$rc = 1
$barWidth = 24
$tick = 0
try {
    while (-not $proc.HasExited) {
        Start-Sleep -Milliseconds 500
        $tick++
        $newLines = Read-NewLines -file $LogFile -state $state
        Update-State -state $state -lines $newLines

        $elapsed = (Get-Date) - $start
        $timeStr = Format-Elapsed $elapsed
        $step = Get-CurrentStepText -state $state
        $prog = Get-Progress -state $state

        if ($prog) {
            # Determinate bar. Cap at 99% while the child is still running so the
            # bar visibly stays "not done" even if all visible steps are marked
            # done but the child hasn't exited yet (compose post-build housekeeping).
            $p = [Math]::Min(0.99, $prog.Progress)
            $filled = [int]([Math]::Round($barWidth * $p))
            if ($filled -lt 0)         { $filled = 0 }
            if ($filled -gt $barWidth) { $filled = $barWidth }
            $bar = ($BAR_FULL * $filled) + ($BAR_EMPTY * ($barWidth - $filled))
            $pct = ([int]($p * 100)).ToString().PadLeft(3)
            $line = "$Label  [$bar] ${pct}%  $timeStr  $($prog.Info)"
        } else {
            # Indeterminate bar: a single full block bounces left-right inside
            # an otherwise empty bar. Cycle length = 2*(barWidth-1), reflecting
            # around the endpoints so it does not pause at the edges.
            $cycle = $tick % (2 * ($barWidth - 1))
            $pulsePos = if ($cycle -lt $barWidth) { $cycle } else { 2 * ($barWidth - 1) - $cycle }
            $bar = ($BAR_EMPTY * $pulsePos) + $BAR_FULL + ($BAR_EMPTY * ($barWidth - $pulsePos - 1))
            $line = "$Label  [$bar]      $timeStr"
        }

        if ($step) {
            $room = ($consoleWidth - 1) - $line.Length - 2
            if ($room -gt 10) {
                $stepTrunc = if ($step.Length -gt $room) { $step.Substring(0, $room) } else { $step }
                $line += "  $stepTrunc"
            }
        }
        if ($line.Length -gt ($consoleWidth - 1)) {
            $line = $line.Substring(0, $consoleWidth - 1)
        }
        $padding = ' ' * [Math]::Max(0, ($consoleWidth - 1) - $line.Length)
        [Console]::Write("`r$line$padding")
    }
    $proc.WaitForExit()
    $rc = $proc.ExitCode
} finally {
    if ($proc -and -not $proc.HasExited) {
        try { $proc.Kill() } catch {}
    }
}

$elapsed = (Get-Date) - $start
$timeStr = Format-Elapsed $elapsed

$clearLine = "`r" + (' ' * ($consoleWidth - 1)) + "`r"
[Console]::Write($clearLine)

if ($rc -eq 0) {
    Write-Host "$Label OK ($timeStr)"
} else {
    Write-Host "$Label FAILED ($timeStr)"
    if (-not $NoTailOnFail) {
        Write-Host ""
        Write-Host "See $LogFile for details. Last 30 lines:"
        Write-Host "----------------------------------------"
        Get-Content -Tail 30 -Encoding UTF8 -Path $LogFile
        Write-Host "----------------------------------------"
    }
}

exit $rc