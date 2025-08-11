@echo off
echo ========================================
echo Kyuubi Testing
echo ========================================

echo.
echo 1. Checking container status...
docker-compose ps

echo.
echo 2. Checking Kyuubi Java processes...
docker exec hadoop-kyuubi jps

echo.
echo 3. Checking Kyuubi Thrift port inside container (10009)...
docker exec hadoop-kyuubi bash -lc "ss -ltnp 2>/dev/null | grep 10009 || netstat -tlnp 2>/dev/null | grep 10009 || true"

echo.
echo 4. Waiting for Kyuubi readiness (port 10009)...
for /l %%i in (1,1,20) do (
  powershell -Command "try { (New-Object Net.Sockets.TcpClient).Connect('localhost',10009); exit 0 } catch { exit 1 }" >nul 2>&1 && goto :ready || (echo Waiting... %%i/20 & timeout /t 3 >nul)
)
:ready

echo.
echo 5. Testing connection via Beeline (engine Spark on YARN, SQL DDL/DML)...
docker exec hadoop-hiveserver2 bash -lc "beeline -u 'jdbc:hive2://kyuubi:10009' -n hadoop -e \"set spark.sql.shuffle.partitions=2; DROP DATABASE IF EXISTS kyuubi_db CASCADE; CREATE DATABASE kyuubi_db; USE kyuubi_db; CREATE TABLE IF NOT EXISTS kyuubi_table (id INT, name STRING); INSERT INTO kyuubi_table VALUES (1, 'k1'), (2, 'k2'); SELECT COUNT(*) AS cnt FROM kyuubi_table; SELECT * FROM kyuubi_table ORDER BY id;\""

echo.
echo 6. Checking HDFS data for kyuubi_table...
docker exec hadoop-namenode bash -lc "hdfs dfs -ls /opt/hive/warehouse/kyuubi_db.db/kyuubi_table || true"
docker exec hadoop-namenode bash -lc "f=\$(hdfs dfs -ls -t /opt/hive/warehouse/kyuubi_db.db/kyuubi_table 2>/dev/null | head -1 | awk '{print \$8}'); if [ -n \"\$f\" ]; then hdfs dfs -cat \"\$f\" | head -n 5; else echo 'No data files found'; fi"

echo.
echo 7. Checking recent YARN applications (SPARK)...
docker exec hadoop-namenode yarn application -list -appStates FINISHED,FAILED

echo.
echo 8. Kyuubi logs (last 80 lines)...
docker-compose logs --tail=80 kyuubi

echo.
echo ========================================
echo Kyuubi Testing completed
echo ========================================
echo.
pause


