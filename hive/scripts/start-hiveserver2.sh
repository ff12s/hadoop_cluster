#!/bin/bash

echo "=== Starting HiveServer2 ==="

# РћР¶РёРґР°РЅРёРµ РіРѕС‚РѕРІРЅРѕСЃС‚Рё Hive Metastore
echo "Waiting for Hive Metastore to be ready..."
until nc -z hive-metastore 9083; do
    echo "Metastore not ready, waiting..."
    sleep 5
done

echo "Hive Metastore is ready!"

# Р—Р°РїСѓСЃРє HiveServer2 РєР°Рє PID 1, Р±РёРЅРґРёРјСЃСЏ РЅР° 0.0.0.0
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
  --hiveconf hive.metastore.warehouse.dir=hdfs://namenode:9000/user/hive/warehouse \
  --hiveconf hive.exec.scratchdir=hdfs://namenode:9000/tmp/hive \
  --hiveconf hive.server2.enable.doAs=false \
  --hiveconf hive.root.logger=INFO,console
