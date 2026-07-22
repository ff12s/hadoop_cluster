@echo off
setlocal EnableExtensions EnableDelayedExpansion
set DOCKER_BUILDKIT=1
set COMPOSE_DOCKER_CLI_BUILD=1
rem Дефолт на случай, если новая ветка кода придёт к :print_help, не задав
rem HELP_EXIT: считаем это ошибкой, как и неизвестный аргумент.
set "HELP_EXIT=2"

set "REGISTRY=fufa242"
for /f "usebackq tokens=1,* delims==" %%A in (`findstr /b /c:"REGISTRY=" .env 2^>nul`) do set "REGISTRY=%%B"

set "DC=docker-compose"
where docker-compose >nul 2>nul
if errorlevel 1 (
    docker compose version >nul 2>nul
    if errorlevel 1 (
        echo ERROR: neither 'docker-compose' nor 'docker compose' is available. Install Docker.
        exit /b 1
    )
    set "DC=docker compose"
)

set "NAMENODE_CONTAINER=hadoop-node"
set "HIVESERVER2_CONTAINER=hadoop-hive"

if not exist ".\scripts\image-tags.ps1" (
    echo ERROR: scripts\image-tags.ps1 not found. Aborting.
    exit /b 1
)
if not exist ".\scripts\run-stage.ps1" (
    echo ERROR: scripts\run-stage.ps1 not found. Aborting.
    exit /b 1
)

set "TAGS_FILE=%TEMP%\hadoop-cluster-image-tags-%RANDOM%-%RANDOM%.env"
powershell -NoProfile -ExecutionPolicy Bypass -File ".\scripts\image-tags.ps1" -Registry "%REGISTRY%" -Format Env -EnvPath ".\.env" > "%TAGS_FILE%"
if errorlevel 1 (
    echo ERROR: image-tags.ps1 failed. Aborting.
    del "%TAGS_FILE%" 2>nul
    exit /b 1
)
for /f "usebackq tokens=1,* delims==" %%A in ("%TAGS_FILE%") do set "%%A=%%B"
del "%TAGS_FILE%" 2>nul
if not defined BASE_REMOTE (
    echo ERROR: BASE_REMOTE not set after image-tags.ps1. Aborting.
    exit /b 1
)

rem Очереди сборки по уровням зависимостей: T1 = base, T2 = FROM base,
rem T3 = FROM spark. Собираются последовательно, иначе дочерний образ может
rem начать сборку раньше, чем локально появится тег его FROM.
set "BUILD_T1="
set "BUILD_T2="
set "BUILD_T3="
set "FORCE_BUILD=0"
set "CLEAN=0"

:parse_args
if "%~1"=="" goto :args_done
if /i "%~1"=="--build" (
    set "FORCE_BUILD=1"
) else if /i "%~1"=="-b" (
    set "FORCE_BUILD=1"
) else if /i "%~1"=="--clean" (
    set "CLEAN=1"
) else if /i "%~1"=="-c" (
    set "CLEAN=1"
) else if /i "%~1"=="clean" (
    echo WARNING: positional 'clean' is deprecated, use --clean instead.
    set "CLEAN=1"
) else if /i "%~1"=="--help" (
    goto :show_help
) else if /i "%~1"=="-h" (
    goto :show_help
) else if /i "%~1"=="/?" (
    goto :show_help
) else (
    echo ERROR: Unknown argument: %~1
    echo.
    goto :show_help_err
)
shift
goto :parse_args
:args_done

rem ===========================================================================
rem Лог: сюда стекается stdout/stderr всех этапов. Спиннер (scripts\run-stage.ps1)
rem читает его хвост, чтобы показать текущий шаг; при падении печатается путь к
rem логу и последние 30 строк.
rem ===========================================================================
if not exist ".\logs" mkdir ".\logs" >nul 2>nul
for /f "usebackq tokens=*" %%T in (`powershell -NoProfile -Command "(Get-Date).ToString('yyyyMMdd-HHmmss')"`) do set "LOG_TS=%%T"
set "LOG_FILE=.\logs\start-cluster-%LOG_TS%.log"
> "%LOG_FILE%" echo === start-cluster.bat log %LOG_TS% ===
echo Log file: %LOG_FILE%
echo.

if "%FORCE_BUILD%"=="1" (
    set "TOTAL=6"
) else (
    set "TOTAL=5"
)

rem ===========================================================================
rem Этап 1: остановка контейнеров (с очисткой по --clean)
rem ===========================================================================
if "%CLEAN%"=="1" (
    call :run_stage "[1/!TOTAL!] Stopping containers and removing volumes" "%DC% down -v --remove-orphans"
    if errorlevel 1 exit /b 1
    call :run_stage "[1/!TOTAL!] Pruning Docker system" "docker system prune -f"
    if errorlevel 1 exit /b 1
) else (
    call :run_stage "[1/!TOTAL!] Stopping containers" "%DC% down --remove-orphans"
    if errorlevel 1 exit /b 1
)

if "%FORCE_BUILD%"=="1" goto :force_build_all
goto :try_pull_then_build

rem ===========================================================================
rem Путь B (--build): три этапа сборки по графу зависимостей:
rem   этап 2: base
rem   этап 3: spark-image, hive  (оба FROM base)
rem   этап 4: jupyter, kyuubi, airflow     (оба FROM spark, airflow копирует из них)
rem --pull не используем: локальные FROM-образы стенда не лежат в registry.
rem ===========================================================================
:force_build_all
call :run_stage "[2/!TOTAL!] Building base" "%DC% build base"
if errorlevel 1 exit /b 1
rem --parallel не передаём: docker compose v2 собирает независимые сервисы
rem параллельно сам и такого флага не принимает.
call :run_stage "[3/!TOTAL!] Building spark-image, hive" "%DC% build spark-image hive"
if errorlevel 1 exit /b 1
call :run_stage "[4/!TOTAL!] Building jupyter, kyuubi, airflow" "%DC% build jupyter kyuubi airflow-image"
if errorlevel 1 exit /b 1
goto :verify_images

rem ===========================================================================
rem Путь A (по умолчанию): тянем готовые образы из Docker Hub, собираем только
rem те, что не стянулись. Неудачный pull молча уходит в очередь сборки.
rem ===========================================================================
:try_pull_then_build
echo.
echo [2/!TOTAL!] Pulling images from Docker Hub
call :pull_or_mark base "%BASE_REMOTE%" "%BASE_IMAGE%" 1
call :pull_or_mark spark-image "%SPARK_REMOTE%" "%SPARK_IMAGE%" 2
call :pull_or_mark hive "%HIVE_REMOTE%" "%HIVE_IMAGE%" 2
call :pull_or_mark jupyter "%JUPYTER_REMOTE%" "%JUPYTER_IMAGE%" 3
call :pull_or_mark kyuubi "%KYUUBI_REMOTE%" "%KYUUBI_IMAGE%" 3
call :pull_or_mark airflow-image "%AIRFLOW_REMOTE%" "%AIRFLOW_IMAGE%" 3

if not defined BUILD_T1 if not defined BUILD_T2 if not defined BUILD_T3 (
    echo.
    echo [3/!TOTAL!] Build skipped - all images pulled.
    >> "%LOG_FILE%" echo [3/!TOTAL!] Build skipped - all images pulled.
    goto :verify_images
)
call :build_tier "!BUILD_T1!" "[3/!TOTAL!] Building missing images"
if errorlevel 1 exit /b 1
call :build_tier "!BUILD_T2!" "[3/!TOTAL!] Building missing images"
if errorlevel 1 exit /b 1
call :build_tier "!BUILD_T3!" "[3/!TOTAL!] Building missing images"
if errorlevel 1 exit /b 1

:verify_images
rem Перед 'up -d --no-build' проверяем, что все нужные локальные теги на месте:
rem ловим расхождение image-tags.ps1 с захардкоженными FROM дочерних Dockerfile'ов.
<nul set /p "_=Verifying image tags... "
set "VERIFY_FAIL=0"
for %%I in ("%BASE_IMAGE%" "%SPARK_IMAGE%" "%HIVE_IMAGE%" "%JUPYTER_IMAGE%" "%KYUUBI_IMAGE%" "%AIRFLOW_IMAGE%") do (
    docker image inspect %%I >> "%LOG_FILE%" 2>&1
    if errorlevel 1 (
        set "VERIFY_FAIL=1"
        >> "%LOG_FILE%" echo MISSING IMAGE: %%I
    )
)
if "!VERIFY_FAIL!"=="1" (
    echo FAILED
    echo One or more required local images are missing.
    call :fail_with_log
    exit /b 1
)
echo OK

rem ===========================================================================
rem Предпоследний этап: запуск сервисов
rem ===========================================================================
set /a "STAGE_UP=TOTAL - 1"
call :run_stage "[!STAGE_UP!/!TOTAL!] Starting services" "%DC% up -d --no-build"
if errorlevel 1 exit /b 1

rem ===========================================================================
rem Последний этап: health-check'и (HDFS и Hive делят один номер этапа)
rem ===========================================================================
call :run_stage "[!TOTAL!/!TOTAL!] Health check: HDFS" "docker exec %NAMENODE_CONTAINER% /opt/scripts/check-hdfs.sh"
if errorlevel 1 exit /b 1
call :run_stage "[!TOTAL!/!TOTAL!] Health check: Hive" "docker exec %HIVESERVER2_CONTAINER% /opt/scripts/check-hive.sh"
if errorlevel 1 exit /b 1

echo.
echo Cluster started successfully!
echo.
echo Web interfaces (via nginx proxy):
echo - HDFS NameNode:         http://localhost:9870
echo - YARN ResourceManager:  http://localhost:8088
echo - YARN Timeline Server:  http://localhost:8188
echo - HDFS DataNode:         http://localhost:9864
echo - YARN NodeManager:      http://localhost:8042
echo - HiveServer2:           http://localhost:10002
echo - TEZ UI:                http://localhost:9999
echo - Spark History:         http://localhost:18080
echo.
echo Web interfaces (direct):
echo - JupyterLab:            http://localhost:8888
echo - Airflow:               http://localhost:8080  (default login: admin/admin)
echo.
echo Test scripts:
echo - HDFS tests:        .\tests\test-hdfs.bat
echo - YARN tests:        .\tests\test-yarn.bat
echo - Full cluster test: .\tests\test-cluster.bat
echo - Airflow tests:     .\tests\test-airflow.bat
exit /b 0

rem ===========================================================================
rem Вспомогательные подпрограммы
rem ===========================================================================

:show_help
set "HELP_EXIT=0"
goto :print_help
:show_help_err
set "HELP_EXIT=2"
:print_help
echo Usage: start-cluster.bat [options]
echo.
echo Options:
echo   -b, --build    Force rebuild of all images (skips pulling from Docker Hub).
echo                  Use this to refresh images after Dockerfile or version changes.
echo   -c, --clean    Remove volumes and prune Docker system before starting.
echo                  WARNING: this deletes all cluster data (HDFS, Hive metastore, etc).
echo   -h, --help     Show this help and exit.
echo.
echo Examples:
echo   start-cluster.bat                  Pull prebuilt images, build only missing ones.
echo   start-cluster.bat --clean          Wipe volumes, then pull/build as usual.
echo   start-cluster.bat --build          Rebuild everything from scratch (no pulling).
echo   start-cluster.bat --clean --build  Wipe volumes and rebuild everything.
echo.
echo Logs: full stdout/stderr of every stage goes to .\logs\start-cluster-*.log
exit /b %HELP_EXIT%

:pull_or_mark
rem %~1 = сервис, %~2 = удалённый тег, %~3 = локальный тег, %~4 = уровень сборки (1..3).
rem -NoTailOnFail: промах pull'а не засоряет экран, дамп лога всё равно даст
rem этап сборки, если и он не справится.
>> "%LOG_FILE%" echo === STAGE: pull %~1 === %TIME%
powershell -NoProfile -ExecutionPolicy Bypass -File ".\scripts\run-stage.ps1" -Label "   %~1" -LogFile "%LOG_FILE%" -Command "docker pull %~2" -NoTailOnFail
if errorlevel 1 (
    call :mark_for_build "%~1" "%~4"
    exit /b 0
)
docker tag %~2 %~3 >> "%LOG_FILE%" 2>&1
if errorlevel 1 (
    echo    %~1: tag step failed, will rebuild locally
    call :mark_for_build "%~1" "%~4"
    exit /b 0
)
exit /b 0

:mark_for_build
rem %~1 = сервис, %~2 = уровень сборки (1..3).
if defined BUILD_T%~2 (
    set "BUILD_T%~2=!BUILD_T%~2! %~1"
) else (
    set "BUILD_T%~2=%~1"
)
exit /b 0

:build_tier
rem %~1 = сервисы одного уровня зависимостей (может быть пусто), %~2 = метка этапа.
if "%~1"=="" exit /b 0
call :run_stage "%~2: %~1" "%DC% build %~1"
exit /b %errorlevel%

:run_stage
rem %~1 = метка этапа, %~2 = одна командная строка.
rem Маркер этапа пишем здесь (в OEM-кодировке cmd, как и вывод docker следом),
rem дальше run-stage.ps1 льёт stdout/stderr команды в %LOG_FILE%, рисует спиннер
rem и при падении сам печатает хвост лога.
>> "%LOG_FILE%" echo === STAGE: %~1 === %TIME%
powershell -NoProfile -ExecutionPolicy Bypass -File ".\scripts\run-stage.ps1" -Label "%~1" -LogFile "%LOG_FILE%" -Command "%~2"
exit /b %errorlevel%

:fail_with_log
echo.
echo See %LOG_FILE% for details. Last 30 lines:
echo ----------------------------------------
powershell -NoProfile -Command "[Console]::OutputEncoding=[Text.Encoding]::UTF8; Get-Content -Tail 30 -Encoding UTF8 -Path '%LOG_FILE%'"
echo ----------------------------------------
exit /b 0
