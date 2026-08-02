@echo off
setlocal EnableExtensions
rem ===========================================================================
rem Заливает в HDFS три jar'а: openlineage-spark, кастомный namespace-резолвер
rem и JDBC-драйвер PostgreSQL. Прод-подобно: на проде jar лежит в
rem HDFS, а не под SPARK_HOME. Airflow-джобы (deploy-mode=cluster) берут его оттуда
rem через --jars (cluster policy в airflow/config/ol_policy, URI — из поля
rem openlineage_jar Variable openlineage_config), т.к. из airflow-образа jar удалён.
rem
rem Источник — запечённый jar на hadoop-node (spark-образ). Идемпотентно (put -f).
rem Вызывается автоматически из start-cluster.bat; можно запускать и вручную после
rem поднятия кластера.
rem ===========================================================================

set "HDFS_DIR=/opt/openlineage"

docker ps --filter "name=^hadoop-node$" --format "{{.Names}}" | findstr /r "." >nul
if errorlevel 1 (
    echo ERROR: container hadoop-node is not running. Start the cluster first.
    exit /b 1
)

echo Seeding openlineage-spark jar into HDFS %HDFS_DIR% ...
docker exec hadoop-node bash -lc "set -e; src=$(ls /opt/spark/jars/openlineage-spark_*.jar 2>/dev/null | head -n1); if [ -z \"$src\" ]; then echo 'ERROR: openlineage-spark jar not found under /opt/spark/jars on hadoop-node'; exit 1; fi; hdfs dfs -mkdir -p %HDFS_DIR%; hdfs dfs -put -f \"$src\" %HDFS_DIR%/; hdfs dfs -ls %HDFS_DIR%"
if errorlevel 1 (
    echo ERROR: failed to seed OpenLineage jar into HDFS.
    exit /b 1
)

rem Resolver собирается на хосте (Maven), в образах его нет: копируем в контейнер
rem и оттуда заливаем в HDFS рядом с openlineage-spark jar.
set "RESOLVER_JAR=spark\jars\openlineage-namespace-resolver.jar"
if not exist "%RESOLVER_JAR%" (
    echo ERROR: %RESOLVER_JAR% not found. Build it: cd openlineage-namespace-resolver ^&^& bash mvnd.sh package
    exit /b 1
)
echo Seeding namespace resolver jar into HDFS %HDFS_DIR% ...
docker cp "%RESOLVER_JAR%" hadoop-node:/tmp/openlineage-namespace-resolver.jar
if errorlevel 1 (
    echo ERROR: failed to copy resolver jar into hadoop-node.
    exit /b 1
)
docker exec hadoop-node bash -lc "set -e; hdfs dfs -put -f /tmp/openlineage-namespace-resolver.jar %HDFS_DIR%/; rm -f /tmp/openlineage-namespace-resolver.jar"
if errorlevel 1 (
    echo ERROR: failed to seed resolver jar into HDFS.
    exit /b 1
)

rem JDBC-драйвер PostgreSQL нужен доказательному DAG'у spark_jdbc_lineage_dag:
rem в spark-образе его нет, берём из hive-образа.
docker ps --filter "name=^hadoop-hive$" --format "{{.Names}}" | findstr /r "." >nul
if errorlevel 1 (
    echo ERROR: container hadoop-hive is not running. Start the cluster first.
    exit /b 1
)
echo Seeding PostgreSQL JDBC driver into HDFS %HDFS_DIR% ...
docker cp hadoop-hive:/opt/hive/lib/postgresql-42.2.23.jar "%TEMP%\postgresql-42.2.23.jar"
if errorlevel 1 (
    echo ERROR: failed to copy pgjdbc out of hadoop-hive.
    exit /b 1
)
docker cp "%TEMP%\postgresql-42.2.23.jar" hadoop-node:/tmp/postgresql-42.2.23.jar
if errorlevel 1 (
    echo ERROR: failed to copy pgjdbc into hadoop-node.
    exit /b 1
)
del "%TEMP%\postgresql-42.2.23.jar" 2>nul
docker exec hadoop-node bash -lc "set -e; hdfs dfs -put -f /tmp/postgresql-42.2.23.jar %HDFS_DIR%/; rm -f /tmp/postgresql-42.2.23.jar; hdfs dfs -ls %HDFS_DIR%"
if errorlevel 1 (
    echo ERROR: failed to seed pgjdbc into HDFS.
    exit /b 1
)

echo OpenLineage, resolver and pgjdbc jars seeded into HDFS.
exit /b 0
