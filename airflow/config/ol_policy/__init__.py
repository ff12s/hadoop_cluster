"""Точка входа cluster policy: инъекция OpenLineage в Spark-таски Airflow."""

from __future__ import annotations

from . import callback, logger, operator, parse, probe, utils, variable  # noqa: F401 пути для monkeypatch
from .callback import ol_execute_callback
from .operator import lineage_forced, operator_attrs, passthrough_exceptions
from .parse import inject_openlineage
from .probe import jar_available, jar_path

__all__ = [
    "apply_policy",
    "reset_state",
    "jar_available",
    "jar_path",
    "inject_openlineage",
    "ol_execute_callback",
    "operator_attrs",
    "lineage_forced",
    "passthrough_exceptions",
    "merge_csv",
]


def apply_policy(task: object) -> None:
    """Точка входа cluster policy: пропускает Spark-таски к инъекции, гася свои ошибки.

    :param task: любая таска Airflow; мутируется на месте на этапе парсинга.
    :return: None.
    """
    try:
        operator_cls = operator._spark_submit_operator()
        if operator_cls is None:
            return
        if not isinstance(task, operator_cls):
            if operator._looks_like_spark_submit(task, operator_cls):
                dag_id, task_id = utils.dag_and_task_ids(task)
                logger.warn_once(
                    ("mapped", dag_id, task_id),
                    "OpenLineage не включён: динамический маппинг тасок не поддерживается (%s.%s)",
                    dag_id,
                    task_id,
                )
            return
        parse.inject_openlineage(task)
    except operator.passthrough_exceptions():
        raise
    except Exception:
        dag_id, task_id = utils.dag_and_task_ids(task)
        logger.warn_once(
            ("unexpected", dag_id, task_id),
            "OpenLineage не включён: непредвиденная ошибка cluster policy (%s.%s)",
            dag_id,
            task_id,
            exc_info=True,
        )


def reset_state() -> None:
    """Сбрасывает модульное состояние политики.

    :return: None.
    """
    logger.reset()


merge_csv = utils.merge_csv
