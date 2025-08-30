#!/bin/bash

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

echo "NameNode and ResourceManager started successfully"

tail -f /dev/null
