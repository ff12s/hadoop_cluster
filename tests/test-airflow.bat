@echo off
echo ========================================
echo Airflow Testing
echo ========================================

echo.
echo 1) Container health...
for /f %%S in ('docker inspect -f "{{.State.Health.Status}}" hadoop-airflow-webserver') do set "WEBSERVER_HEALTH=%%S"
echo webserver: %WEBSERVER_HEALTH%
if not "%WEBSERVER_HEALTH%"=="healthy" (
  echo [ERROR] hadoop-airflow-webserver is not healthy
  exit /b 1
)
for /f %%S in ('docker inspect -f "{{.State.Health.Status}}" hadoop-airflow-scheduler') do set "SCHEDULER_HEALTH=%%S"
echo scheduler: %SCHEDULER_HEALTH%
if not "%SCHEDULER_HEALTH%"=="healthy" (
  echo [ERROR] hadoop-airflow-scheduler is not healthy
  exit /b 1
)

echo.
echo 2) Image contents (spark-submit, yarn, java, provider)...
docker exec hadoop-airflow-scheduler bash -lc "command -v spark-submit && command -v yarn && command -v java" || (
  echo [ERROR] Required binaries missing in the Airflow image
  exit /b 1
)
docker exec hadoop-airflow-scheduler python -c "import inspect; from airflow.providers.apache.spark.operators.spark_submit import SparkSubmitOperator as O; p=inspect.signature(O.__init__).parameters; assert 'deploy_mode' not in p, 'provider newer than expected 4.x'; print('provider signature OK')" || (
  echo [ERROR] Spark provider check failed
  exit /b 1
)

echo.
echo 3) DAG import errors (must be empty)...
docker exec hadoop-airflow-scheduler airflow dags list-import-errors

echo.
echo 4) DAGs registered...
docker exec hadoop-airflow-scheduler airflow dags list --output plain | findstr /c:"spark_pi_dag" || (
  echo [ERROR] spark_pi_dag not found
  exit /b 1
)
docker exec hadoop-airflow-scheduler airflow dags list --output plain | findstr /c:"spark_etl_dag" || (
  echo [ERROR] spark_etl_dag not found
  exit /b 1
)

echo.
echo 5) Running spark_pi_dag...
docker exec hadoop-airflow-scheduler airflow dags test spark_pi_dag 2026-07-21 || (
  echo [ERROR] spark_pi_dag run failed
  exit /b 1
)
rem "dags test" не возвращает код ошибки по упавшим таскам (DAG.test() их проглатывает) -
rem состояние тасок нужно проверять отдельно.
docker exec hadoop-airflow-scheduler airflow tasks states-for-dag-run spark_pi_dag 2026-07-21 --output plain | findstr /c:"failed" >nul && (
  echo [ERROR] spark_pi_dag has failed tasks
  exit /b 1
)

echo.
echo 6) YARN application for Pi...
docker exec hadoop-namenode yarn application -list -appStates FINISHED | findstr /c:"airflow_spark_pi" | findstr /c:"SUCCEEDED" >nul || (
  echo [ERROR] airflow_spark_pi did not succeed
  exit /b 1
)

echo.
echo 7) Running spark_etl_dag...
docker exec hadoop-airflow-scheduler airflow dags test spark_etl_dag 2026-07-21 || (
  echo [ERROR] spark_etl_dag run failed
  exit /b 1
)
docker exec hadoop-airflow-scheduler airflow tasks states-for-dag-run spark_etl_dag 2026-07-21 --output plain | findstr /c:"failed" >nul && (
  echo [ERROR] spark_etl_dag has failed tasks
  exit /b 1
)

echo.
echo 8) HDFS artifacts...
docker exec hadoop-namenode hdfs dfs -ls /user/hadoop/airflow_demo/raw.parquet || exit /b 1
docker exec hadoop-namenode hdfs dfs -ls /user/hadoop/airflow_demo/agg.parquet || exit /b 1

echo.
echo 9) Lineage in Marquez...
curl -s "http://localhost:5000/api/v1/namespaces/hdfs%%3A%%2F%%2Fnamenode%%3A9000/datasets?limit=100" | findstr /c:"airflow_demo/agg.parquet" >nul || (
  echo [ERROR] agg.parquet not found in Marquez
  exit /b 1
)
echo Marquez lineage OK

echo.
echo ========================================
echo Airflow Testing completed
echo ========================================
echo.
pause
