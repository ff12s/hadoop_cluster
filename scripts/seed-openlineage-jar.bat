@echo off
setlocal EnableExtensions
rem ===========================================================================
rem Заливает openlineage-spark jar в HDFS. Прод-подобно: на проде jar лежит в
rem HDFS, а не под SPARK_HOME. Airflow-джобы (deploy-mode=cluster) берут его оттуда
rem через spark.jars (cluster policy airflow_local_settings.py + OPENLINEAGE_JAR),
rem т.к. из airflow-образа jar удалён.
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

echo OpenLineage jar seeded into HDFS.
exit /b 0
