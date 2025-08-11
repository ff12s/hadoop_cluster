@echo off
echo ========================================
echo YARN Cluster Testing
echo ========================================

echo.
echo 1. Checking container status...
docker-compose ps

echo.

echo 2. Checking YARN processes...
echo NameNode processes:
docker exec hadoop-namenode jps

echo.
echo DataNode processes:
docker exec hadoop-datanode jps

echo.
echo 3. Checking YARN nodes...
docker exec hadoop-namenode yarn node -list

echo.
echo 4. Checking YARN applications...
echo All applications:
docker exec hadoop-namenode yarn application -list

echo.
echo Finished applications:
docker exec hadoop-namenode yarn application -list -appStates FINISHED

echo.
echo Failed applications:
docker exec hadoop-namenode yarn application -list -appStates FAILED

echo.
echo 5. Testing YARN with MapReduce job...
echo Running wordcount example...
docker exec hadoop-namenode hadoop jar $HADOOP_HOME/share/hadoop/mapreduce/hadoop-mapreduce-examples-3.3.6.jar wordcount /cluster-test/cluster-test.txt /output/wordcount

echo.
echo 6. Checking job results...
docker exec hadoop-namenode hdfs dfs -cat /output/wordcount/part-r-00000

echo.
echo 7. Checking YARN application logs...
echo Recent YARN applications:
docker exec hadoop-namenode yarn application -list -appStates FINISHED,FAILED

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
