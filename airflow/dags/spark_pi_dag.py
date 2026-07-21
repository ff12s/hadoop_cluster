"""Smoke-DAG: отправляет PySpark-джобу вычисления Pi на YARN кластера стенда."""

from __future__ import annotations

import datetime as dt

from airflow import DAG
from airflow.providers.apache.spark.operators.spark_submit import SparkSubmitOperator

# deploy-mode задаётся extra коннекшена spark_yarn: у оператора провайдера 4.x
# параметра deploy_mode нет.
with DAG(
    dag_id="spark_pi_dag",
    description="Проверка связки Airflow -> spark-submit -> YARN",
    start_date=dt.datetime(2026, 1, 1),
    schedule=None,
    catchup=False,
    tags=["spark", "demo"],
) as dag:
    submit_pi = SparkSubmitOperator(
        task_id="submit_pi",
        conn_id="spark_yarn",
        application="/opt/airflow/jobs/pyspark_pi.py",
        application_args=["50"],
        name="airflow_spark_pi",
        verbose=True,
    )
