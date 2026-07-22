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
docker exec hadoop-node hadoop jar $HADOOP_HOME/share/hadoop/mapreduce/hadoop-mapreduce-examples-3.3.6.jar wordcount /cluster-test/cluster-test.txt /output/wordcount

echo.
echo 6. Checking job results...
docker exec hadoop-node hdfs dfs -cat /output/wordcount/part-r-00000

echo.
echo 7. Checking YARN application logs...
echo Recent YARN applications:
docker exec hadoop-node yarn application -list -appStates FINISHED,FAILED

echo.
echo 8. Checking web interfaces...
echo ResourceManager Web UI:
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
