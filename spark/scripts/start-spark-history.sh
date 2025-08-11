#!/bin/bash
set -euo pipefail

echo "=== Starting Spark History Server ==="

# Подготовка директории событий в HDFS
hdfs dfs -mkdir -p /spark-events || true

export SPARK_HISTORY_OPTS="-Dspark.history.fs.logDirectory=hdfs://namenode:9000/spark-events -Dspark.history.ui.port=18080"

${SPARK_HOME}/sbin/start-history-server.sh

echo "Spark History Server UI: http://localhost:18080"

tail -f /opt/spark/logs/* 2>/dev/null || tail -f /dev/null


