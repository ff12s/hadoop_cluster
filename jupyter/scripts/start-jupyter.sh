#!/bin/bash
set -euo pipefail

echo "=== Starting JupyterLab ==="

# Окружение Spark / Hadoop
export SPARK_HOME=/opt/spark
export PATH="$PATH:$SPARK_HOME/bin:$SPARK_HOME/sbin"
export HADOOP_CONF_DIR=/opt/hadoop/etc/hadoop

# Даёт импортировать `from pyspark.sql import SparkSession` из обычного Python-ядра,
# без обёртки pyspark. Запуск jupyter напрямую (а не exec pyspark) не создаёт лишний
# SparkContext на старте ядра — призрачное YARN-приложение "PySparkShell" в UI рядом с
# явно поименованными. Каждый ноутбук поднимает свою SparkSession по требованию с
# осмысленным appName.
PY4J_ZIP=$(ls "$SPARK_HOME"/python/lib/py4j-*-src.zip 2>/dev/null | head -n1)
export PYTHONPATH="$SPARK_HOME/python:${PY4J_ZIP}:${PYTHONPATH:-}"
export PYSPARK_PYTHON=/opt/python/bin/python3

# OpenLineage только для Spark-сессий ноутбуков. OL-конфиг вынесен из общего
# spark-defaults.conf (ломал интерактивный spark-shell). pyspark читает
# PYSPARK_SUBMIT_ARGS при поднятии JVM; Scala spark-shell его не видит.
export OPENLINEAGE_URL="${OPENLINEAGE_URL:-http://marquez:5000}"
export OPENLINEAGE_NAMESPACE="${OPENLINEAGE_NAMESPACE:-hadoop-cluster}"
export PYSPARK_SUBMIT_ARGS="--conf spark.extraListeners=io.openlineage.spark.agent.OpenLineageSparkListener --conf spark.openlineage.transport.type=http --conf spark.openlineage.transport.url=${OPENLINEAGE_URL} --conf spark.openlineage.namespace=${OPENLINEAGE_NAMESPACE} --conf spark.openlineage.columnLineage.datasetLineageEnabled=true pyspark-shell"

# Создаём стартовый ноутбук с примером SparkSession, если каталог пуст
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

# Запускаем JupyterLab напрямую. Ноутбуки создают SparkSession по требованию.
exec jupyter lab \
  --ip=0.0.0.0 --port=8888 --no-browser \
  --ServerApp.token='' --ServerApp.password='' \
  --notebook-dir=/notebooks
