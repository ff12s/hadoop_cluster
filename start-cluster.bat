@echo off
set DOCKER_BUILDKIT=1
set COMPOSE_DOCKER_CLI_BUILD=1

echo "Stopping and cleaning up existing containers..."
docker-compose down

if "%1"=="clean" (
    echo "Cleaning up volumes..."
    docker-compose down -v
    docker system prune -f
)

echo "Building base image..."
docker-compose build base
if %errorlevel% neq 0 (
    echo "ERROR: Failed to build base image. Aborting."
    exit /b %errorlevel%
)

echo "Building spark image..."
docker-compose build spark-image
if %errorlevel% neq 0 (
    echo "ERROR: Failed to build spark image. Aborting."
    exit /b %errorlevel%
)

echo "Building hive, jupyter, kyuubi images..."
docker-compose build hive-metastore jupyter kyuubi
if %errorlevel% neq 0 (
    echo "ERROR: Failed to build cluster images. Aborting."
    exit /b %errorlevel%
)

echo "Starting cluster..."
docker-compose up -d

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
