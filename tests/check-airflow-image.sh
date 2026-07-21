#!/usr/bin/env bash
# Проверка собранного образа Airflow: бинари и провайдер на месте.
set -euo pipefail

IMAGE="${1:-hadoop-cluster-airflow:latest}"

docker run --rm --entrypoint bash "$IMAGE" -lc '
  set -e
  command -v spark-submit
  command -v yarn
  command -v java
  python -c "import airflow.providers.apache.spark.operators.spark_submit as m; print(m.__file__)"
  python -c "
import inspect
from airflow.providers.apache.spark.operators.spark_submit import SparkSubmitOperator
params = inspect.signature(SparkSubmitOperator.__init__).parameters
assert \"deploy_mode\" not in params, \"провайдер новее ожидаемого 4.x: появился deploy_mode\"
print(\"provider signature OK\")
"
'
echo "IMAGE CHECK OK"
