@echo off
echo ========================================
echo Stopping Hadoop Cluster
echo ========================================

echo.
echo 1. Stopping all containers...
docker-compose down

echo.
echo 2. Checking if stopped...
docker-compose ps

echo.
echo 3. Cleanup (optional)...
echo Remove all cluster data? (y/n)
set /p choice=
if /i "%choice%"=="y" (
    echo Removing volumes...
    docker volume rm hadoop_cluster_namenode-data hadoop_cluster_datanode-data hadoop_cluster_namenode-logs hadoop_cluster_datanode-logs 2>nul
    echo Removing images...
    docker rmi hadoop-cluster-hadoop:latest hadoop-cluster-base:latest 2>nul
    echo Removing network...
    docker network rm hadoop_cluster_hadoop-network 2>nul
    echo Cleanup completed.
)

echo.
echo ========================================
echo Cluster stopped!
echo ========================================
echo.
pause
