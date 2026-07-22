@echo off
echo ========================================
echo YARN Cluster Testing
echo ========================================

echo.
echo 1. Checking container status...
docker-compose ps

echo.

echo 2. Checking Hadoop daemon processes (NameNode, DataNode, RM, NM, Timeline)...
docker exec hadoop-node jps

echo.
echo 3. Checking YARN nodes...
docker exec hadoop-node yarn node -list

echo.
echo 4. Checking YARN applications...
echo All applications:
docker exec hadoop-node yarn application -list

echo.
echo Finished applications:
docker exec hadoop-node yarn application -list -appStates FINISHED

echo.
echo Failed applications:
docker exec hadoop-node yarn application -list -appStates FAILED

echo.
echo 5. Testing YARN with MapReduce job...
echo Running wordcount example...
rem cmd не раскрывает $HADOOP_HOME без шелла — заворачиваем команду в bash -lc,
rem чтобы переменная и джоба реально выполнились, а не упали с "JAR does not exist".
docker exec -u hadoop hadoop-node bash -lc "hdfs dfs -rm -r -f -skipTrash /output/wordcount >/dev/null 2>&1; hadoop jar $HADOOP_HOME/share/hadoop/mapreduce/hadoop-mapreduce-examples-*.jar wordcount /cluster-test/cluster-test.txt /output/wordcount"
if errorlevel 1 (
    echo [ERROR] wordcount job failed
    exit /b 1
)

echo.
echo 6. Checking job results...
docker exec -u hadoop hadoop-node hdfs dfs -cat /output/wordcount/part-r-00000

echo.
echo 7. Checking YARN application logs...
echo Recent YARN applications:
docker exec hadoop-node yarn application -list -appStates FINISHED,FAILED

echo.
echo 8. Checking web interfaces...
echo ResourceManager Web UI:
rem Сразу после шага с MapReduce-джобой RM UI может на мгновение отдать
rem транзиентный HTTP 000 — ждём готовности так же, как в test-kyuubi.bat.
for /l %%i in (1,1,20) do (
  curl -sf -o nul http://localhost:8088 >nul 2>&1 && goto :rm_ready || (echo Waiting for ResourceManager UI... %%i/20 & timeout /t 3 >nul)
)
:rm_ready
curl -s -o nul -w "HTTP Status: %%{http_code}\n" http://localhost:8088

echo.
echo NodeManager Web UI:
curl -s -o nul -w "HTTP Status: %%{http_code}\n" http://localhost:8042

echo.
echo ========================================
echo YARN Testing completed
echo ========================================
echo.
echo Web interfaces:
echo - YARN ResourceManager: http://localhost:8088
echo - YARN NodeManager: http://localhost:8042
echo.
pause
