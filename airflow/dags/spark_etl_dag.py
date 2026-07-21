"""Демонстрационный ETL: генерация parquet в HDFS и его агрегация.

Пара тасок даёт связный input -> output лайнидж в Marquez: его отправляет
OpenLineage-листенер, уже сконфигурированный в spark-defaults.conf стенда.
"""

from __future__ import annotations

import datetime as dt

from airflow import DAG
from airflow.providers.apache.spark.operators.spark_submit import SparkSubmitOperator

RAW_PATH = "hdfs:///user/hadoop/airflow_demo/raw.parquet"
AGG_PATH = "hdfs:///user/hadoop/airflow_demo/agg.parquet"

with DAG(
    dag_id="spark_etl_dag",
    description="Генерация и агрегация parquet в HDFS с лайниджем в Marquez",
    start_date=dt.datetime(2026, 1, 1),
    schedule=None,
    catchup=False,
    tags=["spark", "demo"],
) as dag:
    generate = SparkSubmitOperator(
        task_id="generate",
        conn_id="spark_yarn",
        application="/opt/airflow/jobs/etl_generate.py",
        application_args=[RAW_PATH, "1000"],
        name="airflow_etl_generate",
    )

    aggregate = SparkSubmitOperator(
        task_id="aggregate",
        conn_id="spark_yarn",
        application="/opt/airflow/jobs/etl_aggregate.py",
        application_args=[RAW_PATH, AGG_PATH],
        name="airflow_etl_aggregate",
    )

    generate >> aggregate
