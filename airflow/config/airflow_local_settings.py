"""Cluster policy стенда: точка входа, которую Airflow ищет по имени.

Имя файла и имя функции диктует Airflow (``$AIRFLOW_HOME/config`` кладётся в
``sys.path`` бутстрапом ``settings.initialize()``). Логики здесь нет: вся политика
инъекции OpenLineage живёт в соседнем модуле ``ol_policy`` — так её можно
тестировать без Airflow и без установленного Spark-провайдера.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import ol_policy

if TYPE_CHECKING:
    from airflow.models import BaseOperator


def task_policy(task: BaseOperator) -> None:
    """Навешивает OpenLineage на Spark-таски Airflow.

    :param task: любой оператор Airflow; мутируется на месте на этапе парсинга DAG.
    :return: None.
    """
    ol_policy.apply_policy(task)
