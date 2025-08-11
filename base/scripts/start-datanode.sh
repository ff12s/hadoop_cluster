#!/bin/bash

echo "Starting DataNode..."

# Ожидание запуска NameNode
echo "Waiting for NameNode to start..."
sleep 10

# Запуск HDFS DataNode
echo "Starting HDFS DataNode..."
hdfs datanode &

# Ожидание запуска DataNode
sleep 10

# Запуск YARN NodeManager
echo "Starting YARN NodeManager..."
yarn nodemanager &

# Ожидание запуска NodeManager
sleep 10

echo "DataNode and NodeManager started successfully"

# Держим контейнер запущенным
tail -f /dev/null
