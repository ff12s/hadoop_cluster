#!/bin/bash
set -uo pipefail

echo "=== Starting Hive (Metastore + HiveServer2) ==="

# --- Метастор ---------------------------------------------------------------

echo "Waiting for PostgreSQL to be ready..."
until nc -z postgres 5432; do
    echo "PostgreSQL not ready, waiting..."
    sleep 5
done
echo "PostgreSQL is ready!"

# Classpath без TEZ: иначе FsTracer/HTrace конфликтуют с Hadoop 3.3, а TEZ метастору не нужен
export HADOOP_CLASSPATH=$HADOOP_CONF_DIR:$HIVE_HOME/lib/*

echo "Initializing/Upgrading Hive Metastore schema..."
if schematool -dbType postgres -info >/dev/null 2>&1; then
  echo "Metastore schema exists. Upgrading if needed..."
  schematool -dbType postgres -upgradeSchema
  schema_rc=$?
else
  echo "Metastore schema not found. Initializing..."
  schematool -dbType postgres -initSchema
  schema_rc=$?
fi
# Без "-e" ненулевой код schematool сам по себе не роняет скрипт — проверяем явно,
# иначе метастор поднимется поверх не применённой/битой схемы и откажет позже неявно.
if [ "$schema_rc" -ne 0 ]; then
  echo "ERROR: schematool exited with code $schema_rc, metastore schema is not in a known-good state" >&2
  exit 1
fi

echo "Starting Hive Metastore..."
$HIVE_HOME/bin/hive --service metastore &

echo "Waiting for Metastore port 9083..."
for i in {1..24}; do
  if nc -z localhost 9083; then
    echo "Hive Metastore is listening on 9083"
    break
  fi
  echo "Waiting for metastore ($i/24)..."
  sleep 5
done
if ! nc -z localhost 9083; then
  echo "ERROR: metastore did not open port 9083"
  tail -n 200 /opt/hive/logs/* || true
  exit 1
fi

# --- HiveServer2 ------------------------------------------------------------

echo "Waiting for HDFS to be ready..."
until hdfs dfs -test -d /; do
    echo "HDFS not ready, waiting..."
    sleep 5
done

echo "Waiting for HDFS to leave safe mode..."
hdfs dfsadmin -safemode wait

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

hdfs dfs -mkdir -p /tmp/tez/staging
hdfs dfs -chmod -R 777 /tmp/tez

echo "Waiting for YARN Timeline Server to be ready..."
until nc -z namenode 8188; do
    echo "Timeline Server not ready, waiting..."
    sleep 5
done
echo "YARN Timeline Server is ready!"

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
