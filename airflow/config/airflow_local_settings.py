"""Cluster policy стенда: точечная инъекция OpenLineage в Spark-джобы Airflow.

OL-листенер вынесен из общего spark-defaults.conf (он ломал интерактивный
spark-shell). Airflow добавляет OL только своим SparkSubmitOperator-таскам через
cluster policy — так лайнидж пишется без правок в самих DAG'ах.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from airflow.models import BaseOperator


def task_policy(task: "BaseOperator") -> None:
    """Домешивает OpenLineage-конфиг в conf каждого SparkSubmitOperator.

    :param task: любой оператор Airflow; мутируется на месте на этапе парсинга DAG.
    :return: None.
    """
    from airflow.providers.apache.spark.operators.spark_submit import SparkSubmitOperator

    if not isinstance(task, SparkSubmitOperator):
        return

    ol = {
        "spark.extraListeners": "io.openlineage.spark.agent.OpenLineageSparkListener",
        "spark.openlineage.transport.type": "http",
        # `or`, а не default второго аргумента: compose подставляет пустую строку,
        # если переменной нет в .env (OPENLINEAGE_URL: ${OPENLINEAGE_URL}), и тогда
        # os.environ.get(..., default) вернул бы "" — падаем на дефолт как jupyter (:-).
        "spark.openlineage.transport.url": os.environ.get("OPENLINEAGE_URL") or "http://marquez:5000",
        "spark.openlineage.namespace": os.environ.get("OPENLINEAGE_NAMESPACE") or "hadoop-cluster",
        "spark.openlineage.columnLineage.datasetLineageEnabled": "true",
    }
    # conf, заданный в DAG, побеждает: не затираем осознанные переопределения.
    # Провайдер apache-airflow-providers-apache-spark 4.1.1 хранит conf в приватном
    # _conf (публичного conf нет); execute() строит hook именно из self._conf.
    task._conf = {**ol, **(task._conf or {})}
