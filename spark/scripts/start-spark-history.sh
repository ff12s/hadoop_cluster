#!/bin/bash
set -euo pipefail

echo "=== Starting Spark History Server ==="

# Ожидаем доступности NameNode перед обращением к HDFS
echo "Waiting for HDFS (namenode:9000) to become available..."
for i in {1..60}; do
  if hdfs dfs -ls / >/dev/null 2>&1; then
    echo "HDFS is available"
    break
  fi
  echo "HDFS not ready yet... ($i)" && sleep 2
done

# Подготовка директории событий в HDFS
hdfs dfs -mkdir -p /spark-events || true
hdfs dfs -chmod 1777 /spark-events || true

export SPARK_HISTORY_OPTS="-Dspark.history.fs.logDirectory=hdfs://namenode:9000/spark-events -Dspark.history.ui.port=18080"

${SPARK_HOME}/sbin/start-history-server.sh

echo "Spark History Server UI: http://localhost:18080"

tail -f /opt/spark/logs/* 2>/dev/null || tail -f /dev/null


