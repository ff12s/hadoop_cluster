#Requires -Version 5.1
param(
    [string]$Registry = "fufa242",
    [ValidateSet("Env", "Json")]
    [string]$Format = "Env",
    [string]$EnvPath = ".env"
)

$ErrorActionPreference = "Stop"

function Read-DotEnv {
    param([string]$Path)

    if (-not (Test-Path $Path)) { throw ".env not found: $Path" }
    $map = @{}
    Get-Content $Path -Encoding UTF8 | ForEach-Object {
        $line = $_.Trim()
        if ($line -match '^\s*#' -or $line -eq '') { return }
        $i = $line.IndexOf('=')
        if ($i -lt 1) { return }
        $key = $line.Substring(0, $i).Trim()
        $val = $line.Substring($i + 1).Trim()
        $map[$key] = $val
    }
    return $map
}

$v = Read-DotEnv -Path $EnvPath
$required = @(
    "HADOOP_VERSION", "JAVA_VERSION", "HIVE_VERSION", "TEZ_VERSION",
    "SPARK_VERSION", "PYTHON_VERSION", "JUPYTER_VERSION", "KYUUBI_VERSION"
)
foreach ($k in $required) {
    if (-not $v[$k]) { throw "Missing $k in .env" }
}

$pyParts = $v["PYTHON_VERSION"] -split '\.'
if ($pyParts.Count -lt 2) { throw "PYTHON_VERSION must be like 3.12.7" }
$pyMm = "$($pyParts[0]).$($pyParts[1])"

$baseTag = "h$($v['HADOOP_VERSION'])-j$($v['JAVA_VERSION'])"
$sparkTag = "h$($v['HADOOP_VERSION'])-s$($v['SPARK_VERSION'])"
$hiveTag = "h$($v['HADOOP_VERSION'])-hive$($v['HIVE_VERSION'])-tez$($v['TEZ_VERSION'])"
$jupyterTag = "s$($v['SPARK_VERSION'])-py$pyMm-jlab$($v['JUPYTER_VERSION'])"
$kyuubiTag = "k$($v['KYUUBI_VERSION'])-s$($v['SPARK_VERSION'])"

$data = [ordered]@{
    REGISTRY = $Registry

    BASE_IMAGE = "hadoop-cluster-base:latest"
    SPARK_IMAGE = "hadoop-cluster-spark:latest"
    HIVE_IMAGE = "hadoop-cluster-hive:latest"
    JUPYTER_IMAGE = "hadoop-cluster-jupyter:latest"
    KYUUBI_IMAGE = "hadoop-cluster-kyuubi:latest"

    BASE_REMOTE = "$Registry/hadoop-base:$baseTag"
    SPARK_REMOTE = "$Registry/hadoop-spark:$sparkTag"
    HIVE_REMOTE = "$Registry/hadoop-hive-metastore:$hiveTag"
    JUPYTER_REMOTE = "$Registry/hadoop-jupyter:$jupyterTag"
    KYUUBI_REMOTE = "$Registry/hadoop-kyuubi:$kyuubiTag"
}

if ($Format -eq "Json") {
    $data | ConvertTo-Json -Compress
    exit 0
}

foreach ($k in $data.Keys) {
    Write-Output "$k=$($data[$k])"
}
