#Requires -Version 5.1
# Статические проверки модели docker-compose: состав сервисов, container_name,
# сетевые алиасы, профили, тома и публикуемые порты. Стенд поднимать не нужно.

$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

$script:Failed = 0

function Assert-True {
    param([bool]$Condition, [string]$Message)
    if ($Condition) {
        Write-Output "  OK   $Message"
    } else {
        Write-Output "  FAIL $Message"
        $script:Failed++
    }
}

function Get-ComposeModel {
    param([string[]]$Profiles = @())
    # Имя $args занято автоматической переменной PowerShell — использовать нельзя
    $cliArgs = @()
    foreach ($p in $Profiles) { $cliArgs += @("--profile", $p) }
    $cliArgs += @("config", "--format", "json")
    $json = & docker compose @cliArgs
    if ($LASTEXITCODE -ne 0) { throw "docker compose config завершился с кодом $LASTEXITCODE" }
    return ($json | ConvertFrom-Json)
}

function Get-PublishedPorts {
    param($Service)
    return @($Service.ports | ForEach-Object { "$($_.published)" })
}

$cfg = Get-ComposeModel
$services = @($cfg.services.PSObject.Properties.Name)

Write-Output "== Task 1: consolidation of PostgreSQL =="
Assert-True (-not ($services -contains 'marquez-db')) "сервис marquez-db удалён из модели"
Assert-True ($services -contains 'postgres') "сервис postgres присутствует"
Assert-True ($cfg.services.postgres.container_name -eq 'hadoop-postgres') "container_name сервиса postgres = hadoop-postgres"
Assert-True (@($cfg.services.postgres.networks.default.aliases) -contains 'marquez-db') "postgres отвечает на DNS-имя marquez-db"
$pgPorts = Get-PublishedPorts $cfg.services.postgres
Assert-True ($pgPorts -contains '5433') "порт 5433 опубликован на postgres"
Assert-True ($pgPorts -contains '5434') "порт 5434 опубликован на postgres"
Assert-True (-not (@($cfg.volumes.PSObject.Properties.Name) -contains 'marquez-data')) "том marquez-data удалён"

Write-Output ""
if ($script:Failed -gt 0) {
    Write-Output "FAILED: $script:Failed assertion(s)"
    exit 1
}
Write-Output "ALL PASSED"
exit 0
