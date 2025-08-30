#!/bin/bash
set -euo pipefail

echo "=== Starting Hive Metastore ==="

# Waiting for PostgreSQL readiness
echo "Waiting for PostgreSQL to be ready..."
until nc -z postgres 5432; do
    echo "PostgreSQL not ready, waiting..."
    sleep 5
done

echo "PostgreSQL is ready!"

# Initialize/Upgrade metastore schema
echo "Initializing/Upgrading Hive Metastore schema..."
if schematool -dbType postgres -info >/dev/null 2>&1; then
  echo "Metastore schema exists. Upgrading if needed..."
  schematool -dbType postgres -upgradeSchema
else
  echo "Metastore schema not found. Initializing..."
  schematool -dbType postgres -initSchema
fi

# Start Hive Metastore
echo "Starting Hive Metastore..."
export HADOOP_CLASSPATH=$HADOOP_CONF_DIR:$HADOOP_CLASSPATH:$HIVE_HOME/lib/*
$HIVE_HOME/bin/hive --service metastore &

# Wait for startup
sleep 10

# Check status
echo "Checking Metastore status..."
if jps | grep -iq metastore; then
  echo "Hive Metastore started on port 9083"
  echo "Metastore Web UI: http://localhost:9083"
else
  echo "Metastore process not detected by jps yet. Waiting for port 9083..."
  for i in {1..12}; do
    if nc -z localhost 9083; then
      echo "Hive Metastore is listening on 9083"
      break
    fi
    echo "Waiting ($i/12)..."
    sleep 5
  done
  if ! nc -z localhost 9083; then
    echo "Warning: metastore not detected by jps and port 9083 not open yet. Keeping container alive for debugging."
    tail -n 200 /opt/hive/logs/* || true
  fi
fi

# Keep container running
tail -f /dev/null
