@echo off
echo ========================================
echo HDFS Cluster Testing
echo ========================================

echo.
echo 1. Checking container status...
docker-compose ps

echo.
echo 2. Checking Hadoop daemon processes (NameNode, DataNode, RM, NM, Timeline)...
docker exec hadoop-node jps

echo.
echo 3. Checking HDFS status...
docker exec hadoop-node hdfs dfsadmin -report

echo.
echo 4. Checking NameNode Web UI availability...
curl -s -o nul -w "HTTP Status: %%{http_code}\n" http://localhost:9870

echo.
echo 5. Checking DataNode Web UI availability...
curl -s -o nul -w "HTTP Status: %%{http_code}\n" http://localhost:9864

echo.
echo 6. Testing HDFS operations...
echo Creating test directory...
docker exec hadoop-node hdfs dfs -mkdir -p /test-hdfs

echo Creating test file...
docker exec hadoop-node bash -c "echo 'HDFS Test File - %date% %time%' > /tmp/hdfs-test.txt"

echo Uploading file to HDFS...
docker exec hadoop-node hdfs dfs -put /tmp/hdfs-test.txt /test-hdfs/

echo Listing directory contents...
docker exec hadoop-node hdfs dfs -ls /test-hdfs/

echo Reading file from HDFS...
docker exec hadoop-node hdfs dfs -cat /test-hdfs/hdfs-test.txt

echo.
echo 7. Checking HDFS blocks...
docker exec hadoop-node hdfs fsck / -files -blocks

echo.
echo ========================================
echo HDFS Testing completed
echo ========================================
echo.
echo Web interfaces:
echo - NameNode: http://localhost:9870
echo - DataNode: http://localhost:9864
echo.
pause
