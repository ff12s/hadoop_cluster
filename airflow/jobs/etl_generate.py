"""PySpark-джоба: генерирует демонстрационный датасет и пишет его в HDFS как parquet."""

from __future__ import annotations

import sys

from pyspark.sql import SparkSession
from pyspark.sql import functions as F


def generate(output_path: str, rows: int) -> None:
    """Пишет синтетический датасет продаж в parquet.

    :param output_path: путь назначения в HDFS.
    :param rows: количество строк.
    :return: None
    """
    spark = SparkSession.builder.appName("airflow_etl_generate").enableHiveSupport().getOrCreate()
    try:
        df = (
            spark.range(rows)
            .withColumn("region", F.concat(F.lit("region_"), F.col("id") % 5))
            .withColumn("amount", (F.col("id") * 7 % 100).cast("double"))
            .select("id", "region", "amount")
        )
        df.write.mode("overwrite").parquet(output_path)
        print(f"записано строк: {df.count()} -> {output_path}")
    finally:
        spark.stop()


if __name__ == "__main__":
    generate(sys.argv[1], int(sys.argv[2]))
