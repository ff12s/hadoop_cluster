"""PySpark-джоба: читает сырой parquet, агрегирует по региону, пишет результат в HDFS."""

from __future__ import annotations

import sys

from pyspark.sql import SparkSession
from pyspark.sql import functions as F


def aggregate(input_path: str, output_path: str) -> None:
    """Считает суммы и количества по региону и пишет результат в parquet.

    :param input_path: путь к исходному parquet в HDFS.
    :param output_path: путь назначения в HDFS.
    :return: None
    """
    spark = SparkSession.builder.appName("airflow_etl_aggregate").enableHiveSupport().getOrCreate()
    try:
        df = spark.read.parquet(input_path)
        agg = df.groupBy("region").agg(
            F.sum("amount").alias("total_amount"),
            F.count("*").alias("row_count"),
        )
        agg.write.mode("overwrite").parquet(output_path)
        print(f"регионов записано: {agg.count()} -> {output_path}")
    finally:
        spark.stop()


if __name__ == "__main__":
    aggregate(sys.argv[1], sys.argv[2])
