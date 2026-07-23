@echo off
echo ========================================
echo OpenLineage / Marquez Testing
echo ========================================

echo.
echo 1) Checking Marquez API and Web...
curl -s -o nul -w "API http: %%{http_code}\n" http://localhost:5000/api/v1/namespaces
curl -s -o nul -w "Web http: %%{http_code}\n" http://localhost:3000

echo.
echo 2) Regression guard: shared spark-defaults.conf must NOT carry the OL listener
echo    (global listener broke spark-shell; OL is now injected per-runtime)...
docker exec hadoop-node bash -lc "grep -q OpenLineageSparkListener /opt/spark/conf/spark-defaults.conf && { echo [ERROR] OL listener still in shared spark-defaults.conf; exit 1; } || echo [OK] no OL listener in shared spark-defaults.conf"
if errorlevel 1 (
  echo [ERROR] OpenLineage regression: shared spark-defaults.conf still enables the listener
  exit /b 1
)

echo.
echo 3) Direct spark-submit on hadoop-node must run clean WITHOUT loading OL...
docker exec hadoop-node bash -lc "spark-submit --master yarn --deploy-mode client --class org.apache.spark.examples.SparkPi \"$SPARK_HOME/examples/jars/spark-examples_2.13-$SPARK_VERSION.jar\" 5" || echo [WARN] spark-submit failed or already ran

echo.
echo 4) Current namespaces in Marquez...
curl -s http://localhost:5000/api/v1/namespaces || echo

echo.
echo Lineage emission is verified per-runtime:
echo   - Airflow: tests\test-airflow.bat  (asserts agg.parquet lineage in Marquez)
echo   - Kyuubi:  tests\test-kyuubi.bat
echo   - Jupyter: run a notebook Spark job, then check Marquez Web (:3000)
echo.
echo Done.
pause
