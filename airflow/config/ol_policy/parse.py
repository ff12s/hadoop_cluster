"""Парс-фаза: гейты и идемпотентная дозапись колбэка.

Ноль обращений к Variable, метастору и HDFS — резолв уезжает в ``callback``.
"""

from __future__ import annotations

from . import operator, utils
from .logger import warn_once


def inject_openlineage(task: object) -> None:
    """Дописывает колбэк лайниджа в ``on_execute_callback`` проверенной Spark-таски.

    Ничего, кроме списка колбэков, не трогает: ни conf, ни jars, ни DAG.
    Значения приезжают на воркере — см. ``callback``.

    :param task: экземпляр ``SparkSubmitOperator``; мутируется на месте.
    :return: None.
    """
    from . import callback

    dag_id, task_id = utils.dag_and_task_ids(task)
    if operator.operator_attrs(task) is None:
        warn_once(
            ("unknown-layout", dag_id, task_id),
            "OpenLineage не включён: незнакомая раскладка атрибутов оператора (%s.%s)",
            dag_id,
            task_id,
        )
        return
    if operator.lineage_forced(task) is False:
        return

    existing = getattr(task, "on_execute_callback", None)
    callbacks = list(existing) if isinstance(existing, list) else ([] if existing is None else [existing])
    if callback.ol_execute_callback in callbacks:
        return
    task.on_execute_callback = [*callbacks, callback.ol_execute_callback]
