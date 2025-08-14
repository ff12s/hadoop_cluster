#!/bin/bash
set -euo pipefail

echo "=== Spark YARN test (Pi) ==="

spark-submit \
  --master yarn \
  --deploy-mode client \
  --class org.apache.spark.examples.SparkPi \
  $SPARK_HOME/examples/jars/spark-examples_2.12-${SPARK_VERSION}.jar 50


