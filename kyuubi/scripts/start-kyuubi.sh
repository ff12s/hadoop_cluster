#!/bin/bash
set -euo pipefail

echo "=== Starting Apache Kyuubi ==="

export SPARK_HOME=/opt/spark
export KYUUBI_HOME=/opt/kyuubi
export HADOOP_CONF_DIR=/opt/hadoop/etc/hadoop

mkdir -p "$KYUUBI_HOME/logs" "$KYUUBI_HOME/work" "$KYUUBI_HOME/pid"
if [ ! -w "$KYUUBI_HOME/pid" ] || [ ! -w "$KYUUBI_HOME/logs" ] || [ ! -w "$KYUUBI_HOME/work" ]; then
  echo "Folders are not writable by user $(whoami): $KYUUBI_HOME/{logs,work,pid}. Please rebuild image to fix perms."
  ls -ld "$KYUUBI_HOME" "$KYUUBI_HOME/logs" "$KYUUBI_HOME/work" "$KYUUBI_HOME/pid" || true
  exit 1
fi

exec $KYUUBI_HOME/bin/kyuubi run


