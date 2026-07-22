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

$cfg = Get-ComposeModel -Profiles @('build')
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
Write-Output "== Task 2: merge of namenode, datanode and spark-history =="
Assert-True (-not ($services -contains 'namenode')) "сервис namenode удалён из модели"
Assert-True (-not ($services -contains 'datanode')) "сервис datanode удалён из модели"
Assert-True (-not ($services -contains 'spark-history')) "сервис spark-history удалён из модели"
Assert-True ($services -contains 'hadoop') "сервис hadoop присутствует"
Assert-True ($cfg.services.hadoop.container_name -eq 'hadoop-node') "container_name сервиса hadoop = hadoop-node"
$hadoopAliases = @($cfg.services.hadoop.networks.default.aliases)
Assert-True ($hadoopAliases -contains 'namenode') "hadoop отвечает на DNS-имя namenode"
Assert-True ($hadoopAliases -contains 'datanode') "hadoop отвечает на DNS-имя datanode"
Assert-True ($hadoopAliases -contains 'spark-history') "hadoop отвечает на DNS-имя spark-history"
Assert-True ($cfg.services.hadoop.image -eq $cfg.services.'spark-image'.image) "hadoop использует образ spark, а не base"
$declaredVolumes = @($cfg.volumes.PSObject.Properties.Name)
Assert-True (-not ($declaredVolumes -contains 'namenode-logs')) "том namenode-logs заменён"
Assert-True (-not ($declaredVolumes -contains 'datanode-logs')) "том datanode-logs заменён"
Assert-True ($declaredVolumes -contains 'hadoop-logs') "объявлен единый том hadoop-logs"

Write-Output ""
Write-Output "== Task 3: merge of hive-metastore and hiveserver2 =="
Assert-True (-not ($services -contains 'hive-metastore')) "сервис hive-metastore удалён из модели"
Assert-True (-not ($services -contains 'hiveserver2')) "сервис hiveserver2 удалён из модели"
Assert-True ($services -contains 'hive') "сервис hive присутствует"
Assert-True ($cfg.services.hive.container_name -eq 'hadoop-hive') "container_name сервиса hive = hadoop-hive"
$hiveAliases = @($cfg.services.hive.networks.default.aliases)
Assert-True ($hiveAliases -contains 'hive-metastore') "hive отвечает на DNS-имя hive-metastore"
Assert-True ($hiveAliases -contains 'hiveserver2') "hive отвечает на DNS-имя hiveserver2"
$hivePorts = Get-PublishedPorts $cfg.services.hive
Assert-True ($hivePorts -contains '9083') "порт 9083 опубликован на hive"
Assert-True ($hivePorts -contains '10000') "порт 10000 опубликован на hive"

Write-Output ""
Write-Output "== Task 4: TEZ UI served by nginx =="
Assert-True (-not ($services -contains 'tez-ui')) "сервис tez-ui удалён из модели"
Assert-True (@($cfg.volumes.PSObject.Properties.Name) -contains 'tez-ui-static') "том tez-ui-static объявлен"
Assert-True (@($cfg.services.webproxy.networks.default.aliases) -contains 'tez-ui') "webproxy отвечает на DNS-имя tez-ui"
$proxyMounts = @($cfg.services.webproxy.volumes | ForEach-Object { "$($_.source):$($_.target)" })
Assert-True (($proxyMounts -join ' ') -match 'tez-ui-static:/usr/share/nginx/tez-ui') "webproxy монтирует tez-ui-static"
$hiveMounts = @($cfg.services.hive.volumes | ForEach-Object { "$($_.source):$($_.target)" })
Assert-True (($hiveMounts -join ' ') -match 'tez-ui-static:/srv/tez-ui') "hive монтирует tez-ui-static на запись"

Write-Output ""
if ($script:Failed -gt 0) {
    Write-Output "FAILED: $script:Failed assertion(s)"
    exit 1
}
Write-Output "ALL PASSED"
exit 0
