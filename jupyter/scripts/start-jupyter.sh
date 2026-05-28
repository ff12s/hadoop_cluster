#!/bin/bash
set -euo pipefail

echo "=== Starting JupyterLab ==="

# Spark / Hadoop env
export SPARK_HOME=/opt/spark
export PATH="$PATH:$SPARK_HOME/bin:$SPARK_HOME/sbin"
export HADOOP_CONF_DIR=/opt/hadoop/etc/hadoop

# Allow `from pyspark.sql import SparkSession` from a vanilla Python kernel
# without going through the pyspark wrapper. Previously start-jupyter.sh execed
# `pyspark` which eagerly created a SparkSession (and therefore a YARN
# application named "PySparkShell") at kernel startup — visible in YARN UI
# alongside the explicitly-named applications. Starting jupyter directly avoids
# that ghost SparkContext; each notebook creates its own SparkSession on demand
# with a meaningful appName.
PY4J_ZIP=$(ls "$SPARK_HOME"/python/lib/py4j-*-src.zip 2>/dev/null | head -n1)
export PYTHONPATH="$SPARK_HOME/python:${PY4J_ZIP}:${PYTHONPATH:-}"
export PYSPARK_PYTHON=/opt/python/bin/python3

# Create default notebook with spark session helper, if folder empty
if [ -z "$(ls -A /notebooks 2>/dev/null || true)" ]; then
  cat >/notebooks/Welcome.ipynb <<'NB'
{
 "cells": [
  {"cell_type":"markdown","metadata":{},"source":["# Welcome to Jupyter + Spark on YARN\n","Use findspark or SparkSession builder below."]},
  {"cell_type":"code","execution_count":null,"metadata":{},"outputs":[],"source":["import findspark, os\n","findspark.init('/opt/spark')\n","from pyspark.sql import SparkSession\n","spark = SparkSession.builder.master('yarn').appName('JupyterSpark').getOrCreate()\n","spark.range(5).show()\n"]}
 ],
 "metadata": {"kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"}},
 "nbformat": 4,
 "nbformat_minor": 2
}
NB
fi

# Start JupyterLab directly. Notebooks create SparkSession on demand.
exec jupyter lab \
  --ip=0.0.0.0 --port=8888 --no-browser \
  --ServerApp.token='' --ServerApp.password='' \
  --notebook-dir=/notebooks
