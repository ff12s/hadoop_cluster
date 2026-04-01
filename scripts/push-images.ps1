#Requires -Version 5.1
<#
  Теги и push по версиям из .env:
  - base:     h{Hadoop}-j{Java}
  - spark:    h{Hadoop}-s{Spark}
  - hive:     h{Hadoop}-hive{Hive}-tez{Tez}
  - jupyter:  s{Spark}-py{PyM.m}-jlab{JupyterLab}
  - kyuubi:   k{Kyuubi}-s{Spark}

  Перед push: docker compose build base spark-image hive-metastore jupyter kyuubi
  и docker login.
#>
param(
    [string]$Registry = "fufa242",
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $Root
Set-Location $ProjectRoot

$tagsJson = & (Join-Path $Root "image-tags.ps1") -Registry $Registry -Format Json -EnvPath (Join-Path $ProjectRoot ".env")
if ($LASTEXITCODE -ne 0 -or -not $tagsJson) { throw "Failed to resolve tags from image-tags.ps1" }
$t = $tagsJson | ConvertFrom-Json

$mappings = @(
    @{ Name = "base";            Local = $t.BASE_IMAGE;    Remote = $t.BASE_REMOTE }
    @{ Name = "spark";           Local = $t.SPARK_IMAGE;   Remote = $t.SPARK_REMOTE }
    @{ Name = "hive-metastore";  Local = $t.HIVE_IMAGE;    Remote = $t.HIVE_REMOTE }
    @{ Name = "jupyter";         Local = $t.JUPYTER_IMAGE; Remote = $t.JUPYTER_REMOTE }
    @{ Name = "kyuubi";          Local = $t.KYUUBI_IMAGE;  Remote = $t.KYUUBI_REMOTE }
)

Write-Host "Computed tags:"
foreach ($m in $mappings) {
    Write-Host "  $($m.Name) -> $($m.Remote)"
}
Write-Host ""

foreach ($m in $mappings) {
    $cmdTag = "docker tag $($m.Local) $($m.Remote)"
    $cmdPush = "docker push $($m.Remote)"
    if ($DryRun) {
        Write-Host $cmdTag
        Write-Host $cmdPush
        continue
    }
    docker tag $m.Local $m.Remote
    if ($LASTEXITCODE -ne 0) { throw "docker tag failed: $cmdTag" }
    docker push $m.Remote
    if ($LASTEXITCODE -ne 0) { throw "docker push failed: $cmdPush" }
}

if (-not $DryRun) {
    Write-Host "Done."
}
