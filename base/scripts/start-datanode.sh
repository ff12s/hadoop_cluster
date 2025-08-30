#!/bin/bash

echo "Starting DataNode..."

echo "Waiting for NameNode to start..."
sleep 10

echo "Starting HDFS DataNode..."
hdfs datanode &

sleep 10

echo "Starting YARN NodeManager..."
yarn nodemanager &

sleep 10

echo "DataNode and NodeManager started successfully"

tail -f /dev/null
