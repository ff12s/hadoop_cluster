@echo off
echo "Stopping and cleaning up existing containers..."
docker-compose down

if "%1"=="clean" (
    echo "Cleaning up volumes..."
    docker-compose down -v
    docker system prune -f
)

echo "Building base image..."
docker build -t hadoop-cluster-base:latest ./base

echo "Building cluster images..."
docker-compose --env-file env_file build spark-image
docker-compose --env-file env_file build

echo "Starting cluster..."
docker-compose --env-file env_file up -d

echo "Checking HDFS health..."
docker exec hadoop-namenode /opt/scripts/check-hdfs.sh

echo "Checking Hive health..."
docker exec hadoop-hiveserver2 /opt/scripts/check-hive.sh

echo.
echo "Cluster started successfully!"
echo.
echo "Web interfaces:"
echo "- HDFS NameNode: http://localhost:9870"
echo "- YARN ResourceManager: http://localhost:8088"
echo "- HDFS DataNode: http://localhost:9864"
echo "- HiveServer2: http://localhost:10002"
echo.
echo "Test scripts:"
echo "- HDFS tests: .\tests\test-hdfs.bat"
echo "- YARN tests: .\tests\test-yarn.bat"
echo "- Full cluster test: .\tests\test-cluster.bat"
