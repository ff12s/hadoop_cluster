@echo off
echo ========================================
echo Spark Testing
echo ========================================

echo.
echo 1. Checking containers...
docker-compose ps

echo.
echo 2. Checking Spark History UI...
curl -s -o nul -w "HTTP Status: %%{http_code}\n" http://localhost:18080

echo.
echo 3. Submitting Spark Pi (Scala) to YARN...
docker exec hadoop-spark-history bash -lc "JAR12=\"$SPARK_HOME/examples/jars/spark-examples_2.12-$SPARK_VERSION.jar\"; JAR13=\"$SPARK_HOME/examples/jars/spark-examples_2.13-$SPARK_VERSION.jar\"; if [ -f \"$JAR12\" ]; then JAR=\"$JAR12\"; elif [ -f \"$JAR13\" ]; then JAR=\"$JAR13\"; else echo 'spark-examples jar not found' && exit 1; fi; spark-submit --master yarn --deploy-mode client --class org.apache.spark.examples.SparkPi \"$JAR\" 20"

echo.
echo 4. Submitting PySpark Pi to YARN...
docker exec hadoop-spark-history bash -lc "spark-submit --master yarn --deploy-mode client /opt/scripts/pyspark_pi.py 50"

echo.
echo 5. Checking YARN applications (recent)...
docker exec hadoop-namenode yarn application -list -appStates FINISHED,FAILED

echo.
echo ========================================
echo Spark Testing completed
echo ========================================
echo.
pause


