"""Доказательный DAG резолвера namespace: датасет с невалидным для Marquez namespace.

JDBC URL указывает на два сетевых алиаса одного и того же контейнера PostgreSQL,
поэтому подключение работает, а namespace датасета содержит запятую, которой нет
в charset Marquez. Без namespace-резолвера событие отвергается, с ним — принимается.
"""

from __future__ import annotations

import datetime as dt

from airflow import DAG
from airflow.providers.apache.spark.operators.spark_submit import SparkSubmitOperator

JDBC_URL = "jdbc:postgresql://postgres:5432,marquez-db:5432/hive_metastore"
TABLE = "public.ol_demo_sales"
AGG_PATH = "hdfs:///user/hadoop/airflow_demo/jdbc_agg.parquet"
PGJDBC_JAR = "hdfs://namenode:9000/opt/openlineage/postgresql-42.2.23.jar"

with DAG(
    dag_id="spark_jdbc_lineage_dag",
    description="Round-trip через JDBC с multi-host URL для проверки namespace-резолвера",
    start_date=dt.datetime(2026, 1, 1),
    schedule=None,
    catchup=False,
    tags=["spark", "openlineage", "resolver"],
) as dag:
    jdbc_roundtrip = SparkSubmitOperator(
        task_id="jdbc_roundtrip",
        conn_id="spark_yarn",
        application="/opt/airflow/jobs/etl_jdbc_multihost.py",
        application_args=[JDBC_URL, TABLE, AGG_PATH],
        jars=PGJDBC_JAR,
        name="airflow_jdbc_multihost",
    )
