@echo off
echo ========================================
echo HDFS Cluster Testing
echo ========================================

echo.
echo 1. Checking container status...
docker-compose ps

echo.
echo 2. Checking NameNode processes...
docker exec hadoop-namenode jps

echo.
echo 3. Checking DataNode processes...
docker exec hadoop-datanode jps

echo.
echo 4. Checking HDFS status...
docker exec hadoop-namenode hdfs dfsadmin -report

echo.
echo 5. Checking NameNode Web UI availability...
curl -s -o nul -w "HTTP Status: %%{http_code}\n" http://localhost:9870

echo.
echo 6. Checking DataNode Web UI availability...
curl -s -o nul -w "HTTP Status: %%{http_code}\n" http://localhost:9864

echo.
echo 7. Testing HDFS operations...
echo Creating test directory...
docker exec hadoop-namenode hdfs dfs -mkdir -p /test-hdfs

echo Creating test file...
docker exec hadoop-namenode bash -c "echo 'HDFS Test File - %date% %time%' > /tmp/hdfs-test.txt"

echo Uploading file to HDFS...
docker exec hadoop-namenode hdfs dfs -put /tmp/hdfs-test.txt /test-hdfs/

echo Listing directory contents...
docker exec hadoop-namenode hdfs dfs -ls /test-hdfs/

echo Reading file from HDFS...
docker exec hadoop-namenode hdfs dfs -cat /test-hdfs/hdfs-test.txt

echo.
echo 8. Checking HDFS blocks...
docker exec hadoop-namenode hdfs fsck / -files -blocks

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
