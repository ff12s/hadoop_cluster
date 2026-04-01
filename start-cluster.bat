@echo off
setlocal EnableExtensions EnableDelayedExpansion
set DOCKER_BUILDKIT=1
set COMPOSE_DOCKER_CLI_BUILD=1
set REGISTRY=fufa242

if not exist ".\scripts\image-tags.ps1" (
    echo "ERROR: scripts\image-tags.ps1 not found. Aborting."
    exit /b 1
)

for /f "usebackq tokens=1,* delims==" %%A in (`powershell -NoProfile -ExecutionPolicy Bypass -File ".\scripts\image-tags.ps1" -Registry "%REGISTRY%" -Format Env -EnvPath ".\.env"`) do (
    set "%%A=%%B"
)
if errorlevel 1 (
    echo "ERROR: Failed to resolve image tags from scripts\image-tags.ps1. Aborting."
    exit /b 1
)

set BUILD_SERVICES=

echo "Stopping and cleaning up existing containers..."
docker-compose down

if "%1"=="clean" (
    echo "Cleaning up volumes..."
    docker-compose down -v
    docker system prune -f
)

echo "Trying to pull prebuilt images from Docker Hub..."
call :pull_or_mark base "%BASE_REMOTE%" "%BASE_IMAGE%"
call :pull_or_mark spark-image "%SPARK_REMOTE%" "%SPARK_IMAGE%"
call :pull_or_mark hive-metastore "%HIVE_REMOTE%" "%HIVE_IMAGE%"
call :pull_or_mark jupyter "%JUPYTER_REMOTE%" "%JUPYTER_IMAGE%"
call :pull_or_mark kyuubi "%KYUUBI_REMOTE%" "%KYUUBI_IMAGE%"

if defined BUILD_SERVICES (
    echo "Building missing images:%BUILD_SERVICES%"
    docker-compose build %BUILD_SERVICES%
    if %errorlevel% neq 0 (
        echo "ERROR: Failed to build missing images. Aborting."
        exit /b %errorlevel%
    )
) else (
    echo "All required images were pulled successfully."
)

echo "Starting cluster..."
docker-compose up -d --no-build

echo "Checking HDFS health..."
docker exec hadoop-namenode /opt/scripts/check-hdfs.sh

echo "Checking Hive health..."
docker exec hadoop-hiveserver2 /opt/scripts/check-hive.sh

echo.
echo "Cluster started successfully!"
echo.
echo "Web interfaces (all via nginx proxy):"
echo "- HDFS NameNode:         http://localhost:9870"
echo "- YARN ResourceManager:  http://localhost:8088"
echo "- YARN Timeline Server:  http://localhost:8188"
echo "- HDFS DataNode:         http://localhost:9864"
echo "- YARN NodeManager:      http://localhost:8042"
echo "- HiveServer2:           http://localhost:10002"
echo "- TEZ UI:                http://localhost:9999"
echo "- Spark History:         http://localhost:18080"
echo "- JupyterLab:            http://localhost:8888"
echo.
echo "Test scripts:"
echo "- HDFS tests: .\tests\test-hdfs.bat"
echo "- YARN tests: .\tests\test-yarn.bat"
echo "- Full cluster test: .\tests\test-cluster.bat"
exit /b 0

:pull_or_mark
echo.
echo "Service %~1: pulling %~2"
docker pull %~2
if errorlevel 1 (
    echo "Image not found or pull failed, will build service %~1"
    set "BUILD_SERVICES=!BUILD_SERVICES! %~1"
    exit /b 0
)
docker tag %~2 %~3
if errorlevel 1 (
    echo "Failed to tag %~2 as %~3, will build service %~1"
    set "BUILD_SERVICES=!BUILD_SERVICES! %~1"
    exit /b 0
)
echo "Using pulled image for %~1"
exit /b 0
