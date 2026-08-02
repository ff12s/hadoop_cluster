"""PySpark-джоба: round-trip через JDBC с multi-host URL и агрегация в HDFS.

Multi-host URL выбран намеренно: OpenLineage строит из него dataset namespace
``postgres://host1:port,host2:port``, а запятой нет в charset Marquez, поэтому
без namespace-резолвера событие отвергается целиком.
"""

from __future__ import annotations

import sys

from pyspark.sql import DataFrame, SparkSession
from pyspark.sql import functions as F

_PROPERTIES = {"user": "hive", "password": "hive", "driver": "org.postgresql.Driver"}


def seed_table(spark: SparkSession, jdbc_url: str, table: str) -> None:
    """Создаёт и наполняет исходную таблицу в PostgreSQL.

    :param spark: активная сессия Spark.
    :param jdbc_url: JDBC URL, возможно с несколькими хостами через запятую.
    :param table: имя таблицы вида ``public.ol_demo_sales``.
    :return: None
    """
    rows = (
        spark.range(200)
        .withColumn("region", F.concat(F.lit("region_"), F.col("id") % 4))
        .withColumn("amount", (F.col("id") * 13 % 97).cast("double"))
        .select("id", "region", "amount")
    )
    rows.write.jdbc(url=jdbc_url, table=table, mode="overwrite", properties=_PROPERTIES)


def aggregate(spark: SparkSession, jdbc_url: str, table: str) -> DataFrame:
    """Читает таблицу из PostgreSQL и агрегирует суммы по региону.

    :param spark: активная сессия Spark.
    :param jdbc_url: JDBC URL, возможно с несколькими хостами через запятую.
    :param table: имя исходной таблицы.
    :return: датафрейм с колонками ``region`` и ``total``.
    """
    source = spark.read.jdbc(url=jdbc_url, table=table, properties=_PROPERTIES)
    return source.groupBy("region").agg(F.sum("amount").alias("total"))


def main(jdbc_url: str, table: str, output_path: str) -> None:
    """Прогоняет round-trip: запись в PostgreSQL, чтение, агрегация, запись в HDFS.

    :param jdbc_url: JDBC URL, возможно с несколькими хостами через запятую.
    :param table: имя таблицы в PostgreSQL.
    :param output_path: путь назначения parquet в HDFS.
    :return: None
    """
    spark = SparkSession.builder.appName("airflow_jdbc_multihost").getOrCreate()
    try:
        seed_table(spark, jdbc_url, table)
        aggregate(spark, jdbc_url, table).write.mode("overwrite").parquet(output_path)
        print(f"агрегат записан: {output_path}")
    finally:
        spark.stop()


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], sys.argv[3])
