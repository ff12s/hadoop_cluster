#!/bin/bash

echo "=== Starting HiveServer2 ==="

# Ожидание готовности Hive Metastore
echo "Waiting for Hive Metastore to be ready..."
until nc -z hive-metastore 9083; do
    echo "Metastore not ready, waiting..."
    sleep 5
done

echo "Hive Metastore is ready!"

# Запуск HiveServer2 как PID 1, биндимся на 0.0.0.0
echo "Starting HiveServer2..."
export HADOOP_CLASSPATH=$HADOOP_CONF_DIR:$HADOOP_CLASSPATH:$HIVE_HOME/lib/*
export HIVE_LOG_DIR=/opt/hive/logs
export HIVE_OPTS="-hiveconf hive.root.logger=INFO,console"
exec $HIVE_HOME/bin/hiveserver2 \
  --hiveconf hive.server2.transport.mode=binary \
  --hiveconf hive.server2.thrift.bind.host=0.0.0.0 \
  --hiveconf hive.server2.thrift.port=10000 \
  --hiveconf hive.server2.webui.port=10002 \
  --hiveconf hive.server2.webui.host=0.0.0.0 \
  --hiveconf hive.metastore.uris=thrift://hive-metastore:9083 \
  --hiveconf hive.metastore.warehouse.dir=/opt/hive/warehouse \
  --hiveconf hive.exec.scratchdir=/opt/hive/tmp \
  --hiveconf hive.server2.enable.doAs=false \
  --hiveconf hive.root.logger=INFO,console
