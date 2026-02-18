#!/bin/bash

echo "=== Starting HiveServer2 ==="

# Waiting for Hive Metastore readiness
echo "Waiting for Hive Metastore to be ready..."
until nc -z hive-metastore 9083; do
    echo "Metastore not ready, waiting..."
    sleep 5
done

echo "Hive Metastore is ready!"

# Wait for HDFS to be available and out of safe mode
echo "Waiting for HDFS to be ready..."
until hdfs dfs -test -d /; do
    echo "HDFS not ready, waiting..."
    sleep 5
done

echo "Waiting for HDFS to leave safe mode..."
hdfs dfsadmin -safemode wait

# Upload TEZ libraries to HDFS if not already present
echo "Checking TEZ libraries in HDFS..."
hdfs dfs -mkdir -p /apps/tez
if ! hdfs dfs -test -e /apps/tez/tez.tar.gz; then
    echo "Uploading TEZ libraries to HDFS..."
    if [ -f "$TEZ_HOME/share/tez.tar.gz" ]; then
        hdfs dfs -put "$TEZ_HOME/share/tez.tar.gz" /apps/tez/tez.tar.gz
        echo "TEZ libraries uploaded to /apps/tez/tez.tar.gz"
    else
        echo "WARNING: TEZ archive not found at $TEZ_HOME/share/tez.tar.gz"
        echo "Searching for TEZ archive..."
        TEZ_ARCHIVE=$(find $TEZ_HOME -name "tez*.tar.gz" -type f 2>/dev/null | head -1)
        if [ -n "$TEZ_ARCHIVE" ]; then
            hdfs dfs -put "$TEZ_ARCHIVE" /apps/tez/tez.tar.gz
            echo "TEZ libraries uploaded from $TEZ_ARCHIVE"
        else
            echo "ERROR: No TEZ archive found. TEZ jobs may fail!"
        fi
    fi
else
    echo "TEZ libraries already present in HDFS"
fi

# Create TEZ staging directory in HDFS
hdfs dfs -mkdir -p /tmp/tez/staging
hdfs dfs -chmod -R 777 /tmp/tez

# Wait for YARN Timeline Server (TEZ/ATS integration can block HiveServer2 until ATS is up)
echo "Waiting for YARN Timeline Server to be ready..."
until nc -z namenode 8188; do
    echo "Timeline Server not ready, waiting..."
    sleep 5
done
echo "YARN Timeline Server is ready!"

# Start HiveServer2 as PID 1, bind to 0.0.0.0
echo "Starting HiveServer2..."
export HADOOP_CLASSPATH=$HADOOP_CONF_DIR:$HADOOP_HOME/share/hadoop/common/*:$HADOOP_HOME/share/hadoop/common/lib/*:$HADOOP_HOME/share/hadoop/hdfs/*:$HADOOP_HOME/share/hadoop/hdfs/lib/*:$TEZ_CONF_DIR:$TEZ_HOME/*:$TEZ_HOME/lib/*:$HIVE_HOME/lib/*
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
