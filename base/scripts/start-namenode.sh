#!/bin/bash

# If running as root, fix timeline-data volume permissions and re-exec as hadoop
if [ "$(id -u)" = "0" ]; then
  mkdir -p /opt/hadoop/timeline-data
  chown -R hadoop:hadoop /opt/hadoop/timeline-data
  exec runuser -u hadoop -- "$0" "$@"
fi

echo "Starting NameNode..."

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

echo "NameNode, ResourceManager and Timeline Server started successfully"

tail -f /dev/null
