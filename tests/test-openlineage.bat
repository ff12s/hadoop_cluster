@echo off
echo ========================================
echo OpenLineage / Marquez Testing
echo ========================================

echo.
echo 1) Checking Marquez API and Web...
curl -s -o nul -w "API http: %%{http_code}\n" http://localhost:5000/api/v1/namespaces
curl -s -o nul -w "Web http: %%{http_code}\n" http://localhost:3000

echo.
echo 2) Ensuring namespace exists via Spark job emission...
docker exec hadoop-spark-history bash -lc "spark-submit --master yarn --deploy-mode client --class org.apache.spark.examples.SparkPi \"$SPARK_HOME/examples/jars/spark-examples_2.13-$SPARK_VERSION.jar\" 5" || echo [WARN] spark-submit failed or already ran

echo.
echo 3) Checking events in Marquez API...
curl -s http://localhost:5000/api/v1/namespaces || echo

echo.
echo Done.
pause


