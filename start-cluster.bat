@echo off
setlocal EnableExtensions EnableDelayedExpansion
set DOCKER_BUILDKIT=1
set COMPOSE_DOCKER_CLI_BUILD=1
rem Defensive default: if a future path lands at :print_help without setting HELP_EXIT,
rem treat it as an error (same posture as the unknown-arg branch).
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

set "NAMENODE_CONTAINER=hadoop-namenode"
set "HIVESERVER2_CONTAINER=hadoop-hiveserver2"

if not exist ".\scripts\image-tags.ps1" (
    echo ERROR: scripts\image-tags.ps1 not found. Aborting.
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

set "BUILD_SERVICES="
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

if "%CLEAN%"=="1" (
    echo Cleaning up volumes and pruning system...
    %DC% down -v --remove-orphans
    docker system prune -f
) else (
    echo Stopping and cleaning up existing containers...
    %DC% down --remove-orphans
)

if "%FORCE_BUILD%"=="1" goto :force_build_all
goto :try_pull_then_build

:force_build_all
echo --build specified: building all images from scratch...
rem Dependency graph:
rem   stage 1: base
rem   stage 2: spark-image, hive-metastore  (both FROM base, run in parallel)
rem   stage 3: jupyter, kyuubi              (both FROM spark, run in parallel)
rem No --pull: local FROM images (hadoop-cluster-base/spark) are not in any registry.

echo.
echo [stage 1/3] Building: base
%DC% build base
if errorlevel 1 (
    echo ERROR: [stage 1/3] build failed: base
    exit /b 1
)

echo.
echo [stage 2/3] Building in parallel: spark-image hive-metastore
%DC% build spark-image hive-metastore
if errorlevel 1 (
    echo ERROR: [stage 2/3] build failed: spark-image hive-metastore
    exit /b 1
)

echo.
echo [stage 3/3] Building in parallel: jupyter kyuubi
%DC% build jupyter kyuubi
if errorlevel 1 (
    echo ERROR: [stage 3/3] build failed: jupyter kyuubi
    exit /b 1
)

goto :verify_images

:try_pull_then_build
echo Trying to pull prebuilt images from Docker Hub...
call :pull_or_mark base "%BASE_REMOTE%" "%BASE_IMAGE%"
call :pull_or_mark spark-image "%SPARK_REMOTE%" "%SPARK_IMAGE%"
call :pull_or_mark hive-metastore "%HIVE_REMOTE%" "%HIVE_IMAGE%"
call :pull_or_mark jupyter "%JUPYTER_REMOTE%" "%JUPYTER_IMAGE%"
call :pull_or_mark kyuubi "%KYUUBI_REMOTE%" "%KYUUBI_IMAGE%"

if defined BUILD_SERVICES (
    echo Building missing images: !BUILD_SERVICES!
    %DC% build !BUILD_SERVICES!
    if errorlevel 1 (
        echo ERROR: Failed to build missing images. Aborting.
        exit /b 1
    )
) else (
    echo All required images were pulled successfully.
)

:verify_images
rem Sanity-check that all required local image tags exist before 'up -d --no-build'.
rem Catches drift if image-tags.ps1 ever stops aligning BASE_IMAGE/etc. with the
rem hardcoded FROM in child Dockerfiles.
for %%I in ("%BASE_IMAGE%" "%SPARK_IMAGE%" "%HIVE_IMAGE%" "%JUPYTER_IMAGE%" "%KYUUBI_IMAGE%") do (
    docker image inspect %%I >nul 2>nul
    if errorlevel 1 (
        echo ERROR: required image %%I not found locally after build/pull. Aborting.
        exit /b 1
    )
)

:after_images
echo Starting cluster...
%DC% up -d --no-build
if errorlevel 1 (
    echo ERROR: '%DC% up' failed. Cluster not started.
    exit /b 1
)

echo Checking HDFS health...
docker exec %NAMENODE_CONTAINER% /opt/scripts/check-hdfs.sh
if errorlevel 1 (
    echo ERROR: HDFS health check failed.
    exit /b 1
)

echo Checking Hive health...
docker exec %HIVESERVER2_CONTAINER% /opt/scripts/check-hive.sh
if errorlevel 1 (
    echo ERROR: Hive health check failed.
    exit /b 1
)

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
echo.
echo Test scripts:
echo - HDFS tests:        .\tests\test-hdfs.bat
echo - YARN tests:        .\tests\test-yarn.bat
echo - Full cluster test: .\tests\test-cluster.bat
exit /b 0

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
exit /b %HELP_EXIT%

:pull_or_mark
echo.
echo Service %~1: pulling %~2
docker pull %~2
if errorlevel 1 (
    echo Image not found or pull failed, will build service %~1
    if defined BUILD_SERVICES (
        set "BUILD_SERVICES=!BUILD_SERVICES! %~1"
    ) else (
        set "BUILD_SERVICES=%~1"
    )
    exit /b 0
)
docker tag %~2 %~3
if errorlevel 1 (
    echo Failed to tag %~2 as %~3, will build service %~1
    if defined BUILD_SERVICES (
        set "BUILD_SERVICES=!BUILD_SERVICES! %~1"
    ) else (
        set "BUILD_SERVICES=%~1"
    )
    exit /b 0
)
echo Using pulled image for %~1
exit /b 0
