#!/bin/bash
set -euo pipefail

echo "=== Starting JupyterLab ==="

# Allow from any host (containerized)
# Use version-agnostic absolute path
export PYSPARK_PYTHON=/opt/python/bin/python3
export PYSPARK_DRIVER_PYTHON=jupyter
export PYSPARK_DRIVER_PYTHON_OPTS="lab --ip=0.0.0.0 --port=8888 --no-browser --ServerApp.token='' --ServerApp.password='' --notebook-dir=/notebooks"

# Spark env
export SPARK_HOME=/opt/spark
export PATH="$PATH:$SPARK_HOME/bin:$SPARK_HOME/sbin"
export HADOOP_CONF_DIR=/opt/hadoop/etc/hadoop

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

# Launch PySpark which will start JupyterLab via PYSPARK_DRIVER_PYTHON_OPTS
exec pyspark --master yarn --deploy-mode client


