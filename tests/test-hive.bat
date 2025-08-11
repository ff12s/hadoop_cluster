@echo off
echo ========================================
echo Hive Cluster Testing
echo ========================================

echo.
echo 1. Checking container status...
docker-compose ps

echo.
echo 2. Checking Hive Metastore processes...
docker exec hadoop-hive-metastore jps

echo.
echo 3. Checking HiveServer2 processes...
docker exec hadoop-hiveserver2 jps

echo.
echo 4. Checking Hive Metastore availability...
curl -s -o nul -w "HTTP Status: %%{http_code}\n" http://localhost:9083

echo.
echo 5. Checking HiveServer2 Web UI availability...
curl -s -o nul -w "HTTP Status: %%{http_code}\n" http://localhost:10002

echo.
echo 6. Testing Hive with Beeline...
echo Dropping test database if exists...
docker exec hadoop-hiveserver2 beeline -u jdbc:hive2://hiveserver2:10000 -n hadoop -e "DROP DATABASE IF EXISTS test_db CASCADE;"

echo Creating test database...
docker exec hadoop-hiveserver2 beeline -u jdbc:hive2://hiveserver2:10000 -n hadoop -e "CREATE DATABASE test_db;"

echo Creating test table...
docker exec hadoop-hiveserver2 beeline -u jdbc:hive2://hiveserver2:10000 -n hadoop -e "USE test_db; CREATE TABLE IF NOT EXISTS test_table (id INT, name STRING);"

echo Inserting test data...
docker exec hadoop-hiveserver2 beeline -u jdbc:hive2://hiveserver2:10000 -n hadoop -e "USE test_db; INSERT INTO test_table VALUES (1, 'Test1'), (2, 'Test2');"

echo Checking YARN application status...
timeout /t 10 /nobreak > nul
docker exec hadoop-namenode yarn application -list -appStates FINISHED

echo Querying test data...
docker exec hadoop-hiveserver2 beeline -u jdbc:hive2://hiveserver2:10000 -n hadoop -e "USE test_db; SELECT * FROM test_table;"

echo.
echo 7. Testing Hive with HDFS integration...
echo Creating HDFS directory for Hive...
docker exec hadoop-namenode hdfs dfs -mkdir -p /user/hive/warehouse

echo Creating external table...
docker exec hadoop-hiveserver2 beeline -u jdbc:hive2://hiveserver2:10000 -n hadoop -e "USE test_db; CREATE EXTERNAL TABLE IF NOT EXISTS hdfs_table (id INT, name STRING) ROW FORMAT DELIMITED FIELDS TERMINATED BY ',' LOCATION '/user/hive/warehouse/hdfs_table';"

echo Creating test data file in HDFS...
docker exec hadoop-namenode bash -c "echo '1,Test1' > /tmp/hdfs_data.csv"
docker exec hadoop-namenode bash -c "echo '2,Test2' >> /tmp/hdfs_data.csv"
docker exec hadoop-namenode bash -c "echo '3,Test3' >> /tmp/hdfs_data.csv"
docker exec hadoop-namenode hdfs dfs -put /tmp/hdfs_data.csv /user/hive/warehouse/hdfs_table/

echo Querying HDFS table...
docker exec hadoop-hiveserver2 beeline -u jdbc:hive2://hiveserver2:10000 -n hadoop -e "USE test_db; SELECT * FROM hdfs_table;"

echo.
echo 8. Checking Hive logs...
echo Hive Metastore logs:
docker-compose logs --tail=5 hive-metastore

echo.
echo HiveServer2 logs:
docker-compose logs --tail=5 hiveserver2

echo.
echo ========================================
echo Hive Testing completed
echo ========================================
echo.
echo Web interfaces:
echo - Hive Metastore: http://localhost:9083
echo - HiveServer2: http://localhost:10002
echo.
pause
