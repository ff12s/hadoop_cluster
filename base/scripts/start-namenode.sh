#!/bin/bash

echo "Starting NameNode..."

# Форматирование NameNode (если не отформатирован)
if [ ! -f /opt/hadoop/dfs/name/current/VERSION ]; then
    echo "Formatting NameNode..."
    hdfs namenode -format
fi

# Запуск HDFS NameNode
echo "Starting HDFS NameNode..."
hdfs namenode &

# Ожидание запуска NameNode
sleep 10

# Запуск YARN ResourceManager
echo "Starting YARN ResourceManager..."
yarn resourcemanager &

# Ожидание запуска ResourceManager
sleep 10

echo "NameNode and ResourceManager started successfully"

# Держим контейнер запущенным
tail -f /dev/null
