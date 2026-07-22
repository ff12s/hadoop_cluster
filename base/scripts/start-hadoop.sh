#!/bin/bash

# Все демоны HDFS/YARN и Spark History Server в одном контейнере.
# Псевдораспределённый режим: каждый демон — отдельный процесс на одной машине.

# При запуске от root чиним права тома timeline-data и переходим в пользователя hadoop
if [ "$(id -u)" = "0" ]; then
  mkdir -p /opt/hadoop/timeline-data
  chown -R hadoop:hadoop /opt/hadoop/timeline-data
  exec runuser -u hadoop -- "$0" "$@"
fi

echo "Starting Hadoop node (NameNode, DataNode, ResourceManager, NodeManager, Timeline, Spark History)..."

if [ ! -f /opt/hadoop/dfs/name/current/VERSION ]; then
    echo "Formatting NameNode..."
    hdfs namenode -format
fi

echo "Starting HDFS NameNode..."
hdfs namenode &

sleep 10

echo "Starting YARN ResourceManager..."
yarn resourcemanager &

sleep 10

echo "Starting YARN Timeline Server..."
mkdir -p /opt/hadoop/timeline-data
yarn timelineserver &

sleep 5

echo "Starting HDFS DataNode..."
hdfs datanode &

sleep 10

echo "Starting YARN NodeManager..."
yarn nodemanager &

sleep 10

# Скрипт сам ждёт готовности HDFS, готовит /spark-events и демонизирует сервер,
# затем держит хвост своих логов — поэтому уходит в фон
echo "Starting Spark History Server..."
/opt/scripts/start-spark-history.sh &

echo "All Hadoop daemons started successfully"

tail -f /dev/null
