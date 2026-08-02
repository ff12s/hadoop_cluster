"""Совместимость с раскладками ``SparkSubmitOperator`` и тумблер лайниджа из ``params``."""

from __future__ import annotations

import importlib
from typing import NamedTuple

from . import utils
from .logger import warn_once

# Имена, а не классы: airflow.exceptions нельзя импортировать на уровне модуля — политика
# подключается раньше, чем airflow.settings завершает инициализацию.
_PASSTHROUGH_NAMES = ("AirflowTaskTimeout", "AirflowClusterPolicyViolation", "AirflowClusterPolicySkipDag")

_ATTR_CANDIDATES: dict[str, tuple[str, ...]] = {"conf": ("conf", "_conf"), "jars": ("jars", "_jars")}


class OperatorAttrs(NamedTuple):
    """Имена атрибутов conf и jars конкретной раскладки оператора."""

    conf: str
    jars: str


def passthrough_exceptions() -> tuple[type[BaseException], ...]:
    """Собирает классы исключений Airflow, которые политика пропускает наружу.

    :return: кортеж классов; пустой, если Airflow недоступен.
    """
    collected: tuple[type[BaseException], ...] = ()
    for name in _PASSTHROUGH_NAMES:
        try:
            collected += (getattr(importlib.import_module("airflow.exceptions"), name),)
        except (ImportError, AttributeError):
            continue
    return collected


def operator_attrs(task: object) -> OperatorAttrs | None:
    """Определяет имена атрибутов conf и jars у этого оператора.

    :param task: таска Airflow.
    :return: имена атрибутов либо None, если раскладка незнакома.
    """
    fields = set(getattr(task, "template_fields", ()) or ())
    resolved: dict[str, str] = {}
    for role, candidates in _ATTR_CANDIDATES.items():
        for name in candidates:
            if name in fields and hasattr(task, name):
                resolved[role] = name
                break
        else:
            return None
    return OperatorAttrs(**resolved)


def _spark_submit_operator() -> type | None:
    """Класс ``SparkSubmitOperator`` установленного провайдера.

    :return: класс оператора либо None, если провайдера в среде нет.
    """
    try:
        from airflow.providers.apache.spark.operators.spark_submit import SparkSubmitOperator
    except Exception:
        return None
    return SparkSubmitOperator


def _looks_like_spark_submit(task: object, operator_cls: type) -> bool:
    """Похожа ли таска на ``SparkSubmitOperator``, не будучи его экземпляром.

    Так распознаются динамически размапленные таски: их conf и jars лежат в
    ``partial_kwargs``, адресация через ``operator_attrs`` на них не работает.

    :param task: таска Airflow.
    :param operator_cls: класс оператора установленного провайдера.
    :return: True, если ``operator_class`` или ``task_type`` указывают на оператор.
    """
    operator_class = getattr(task, "operator_class", None)
    if operator_class is operator_cls:
        return True
    name = operator_class if isinstance(operator_class, str) else getattr(operator_class, "__name__", "")
    return name == operator_cls.__name__ or getattr(task, "task_type", None) == operator_cls.__name__


def _level_forced(owner: object, level: str, dag_id: str, task_id: str) -> bool | None:
    """Читает тумблер ``params['openlineage']`` одного уровня лесенки.

    :param owner: таска либо DAG, чьи ``params`` читаются.
    :param level: имя уровня для сообщения ("таски" либо "DAG'а").
    :param dag_id: идентификатор DAG'а для ключа дедупликации.
    :param task_id: идентификатор таски для ключа дедупликации.
    :return: True, False либо None, если уровень не высказался или значение негодно.
    """
    params = getattr(owner, "params", None)
    if params is None:
        return None
    try:
        if "openlineage" not in params:
            return None
        value = params["openlineage"]
    except Exception:
        warn_once(
            ("unreadable-toggle", dag_id, task_id),
            "OpenLineage: не удалось прочитать params['openlineage'] — уровень %s игнорируется (%s.%s)",
            level,
            dag_id,
            task_id,
        )
        return None
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    warn_once(
        ("bad-toggle", dag_id, task_id),
        "OpenLineage: params['openlineage'] не является булевым — уровень %s игнорируется (%s.%s)",
        level,
        dag_id,
        task_id,
    )
    return None


def lineage_forced(task: object) -> bool | None:
    """Читает форс лайниджа из DAG'а: сначала ``task.params``, затем ``dag.params``.

    :param task: таска Airflow.
    :return: True — форс-включение, False — форс-выключение, None — решает Variable.
    """
    dag_id, task_id = utils.dag_and_task_ids(task)
    forced = _level_forced(task, "таски", dag_id, task_id)
    if forced is not None:
        return forced
    return _level_forced(utils.task_dag(task), "DAG'а", dag_id, task_id)
