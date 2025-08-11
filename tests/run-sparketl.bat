@echo off
setlocal ENABLEDELAYEDEXPANSION

rem Configuration
set JOB_NAME=test
set CLASS_NAME=sparketl.Main
set CONTAINER=hadoop-spark-history
set REMOTE_DIR=/opt/spark/jobs/sparketl

rem Paths
set SCRIPT_DIR=%~dp0
set ROOT_DIR=%SCRIPT_DIR%..\
set ETL_DIR=%ROOT_DIR%sparketl
set ARGS_FILE=%ETL_DIR%\args.json
set DB_NAME=test_etl_dl

if not exist "%ETL_DIR%" (
  echo [ERROR] Folder not found: %ETL_DIR%
  exit /b 1
)
if not exist "%ARGS_FILE%" (
  echo [ERROR] File not found: %ARGS_FILE%
  exit /b 1
)

set JAR_FILE=
for %%F in ("%ETL_DIR%\*.jar") do (
  set JAR_FILE=%%~nxF
  goto :jar_found
)
echo [ERROR] No JAR found in %ETL_DIR%
exit /b 1

:jar_found
set JAR_PATH=%ETL_DIR%\%JAR_FILE%
echo Using JAR: %JAR_PATH%
echo Using args: %ARGS_FILE%

echo.
echo 1) Ensuring target dir in container...
docker exec %CONTAINER% bash -lc "mkdir -p %REMOTE_DIR% && rm -f %REMOTE_DIR%/*.jar %REMOTE_DIR%/args.json" || (
  echo [ERROR] Container %CONTAINER% is not running. Start it first: docker-compose up -d spark-history
  exit /b 1
)

echo.
echo 2) Copying files into container...
docker cp "%JAR_PATH%" %CONTAINER%:%REMOTE_DIR%/ || exit /b 1
docker cp "%ARGS_FILE%" %CONTAINER%:%REMOTE_DIR%/ || exit /b 1

echo.
echo 3) Ensuring Hive database exists...
docker exec hadoop-hiveserver2 bash -lc "beeline -u 'jdbc:hive2://hiveserver2:10000' -n hadoop -e \"CREATE DATABASE IF NOT EXISTS %DB_NAME%; SHOW DATABASES LIKE '%DB_NAME%';\"" || (
  echo [ERROR] Failed to ensure Hive database %DB_NAME%. Make sure HiveServer2 is up.
  exit /b 1
)

echo.
echo 3.1) Ensuring HDFS warehouse path exists...
docker exec hadoop-namenode bash -lc "hdfs dfs -mkdir -p /user/hive/warehouse && hdfs dfs -chmod 1777 /user/hive/warehouse" || echo [WARN] HDFS prep step failed or already exists

echo.
echo 4) Submitting Spark job on YARN (cluster mode)...
docker exec %CONTAINER% bash -lc "spark-submit --name %JOB_NAME% --class %CLASS_NAME% --files %REMOTE_DIR%/args.json --conf spark.sql.catalogImplementation=hive --conf spark.sql.warehouse.dir=hdfs://namenode:9000/user/hive/warehouse --master yarn --deploy-mode cluster %REMOTE_DIR%/%JAR_FILE% args.json" || (
  echo [ERROR] spark-submit failed
  exit /b 1
)

echo.
echo 5) Recent YARN applications:
docker exec hadoop-namenode yarn application -list -appStates NEW,NEW_SAVING,SUBMITTED,ACCEPTED,RUNNING,FINISHED,FAILED,KILLED

echo.
echo Done.
pause


