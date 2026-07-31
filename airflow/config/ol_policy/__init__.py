"""Cluster policy стенда: инъекция OpenLineage в Spark-таски Airflow.

OL-листенер вынесен из общего ``spark-defaults.conf`` (он ломал интерактивный
``spark-shell``), поэтому Airflow навешивает лайнидж своим ``SparkSubmitOperator``
сам — без правок в DAG'ах.

Пакет разложен по фазам жизненного цикла политики: ``parse`` собирает строки на
разборе DAG-файла, ``render`` резолвит значения на воркере, ``variable`` читает
Airflow Variable, ``probe`` ходит в HDFS, ``operator`` знает про две раскладки
провайдера. Здесь остаётся только точка входа и общий сброс состояния.

Политика ничего не роняет: любая ошибка гасится и превращается в «лайниджа нет».
Ни один модуль пакета не импортирует Airflow на уровне модуля — импорт идёт внутри
функций, поэтому набор тестов запускается без установленного Airflow.
"""

from __future__ import annotations

from . import logger
from . import operator
from . import parse
from . import probe
from . import render
from . import utils
from . import variable
from .operator import lineage_forced, operator_attrs, passthrough_exceptions
from .parse import MACRO, inject_openlineage
from .probe import jar_available, jar_path
from .render import ol_macro

__all__ = [
    "apply_policy",
    "reset_state",
    "jar_available",
    "jar_path",
    "inject_openlineage",
    "ol_macro",
    "operator_attrs",
    "lineage_forced",
    "passthrough_exceptions",
    "MACRO",
    "merge_jars",
    "merge_listeners",
]

# Реэкспорты ниже — это read-only алиасы: собственный код пакета их не читает,
# он всегда обращается к атрибуту через модуль-владелец. monkeypatch.setattr(ol_policy, "X", ...)
# поэтому подменяет только эту переменную здесь, а не вызов внутри модуля-владельца —
# патчить нужно submodule (ol_policy.probe.X, ol_policy.render.X и т.д.).


def apply_policy(task: object) -> None:
    """Точка входа cluster policy: гейт типа таски и общий перехват ошибок.

    Любая ошибка политики гасится: исключение отсюда роняет импорт всего
    DAG-файла, то есть баг выключил бы все DAG'и разом. Чужой механизм таймаута
    и чужое решение пропустить DAG пробрасываются наружу.

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
                logger.warn_once(("mapped", dag_id, task_id), "OpenLineage не включён: динамический маппинг тасок не поддерживается (%s.%s)", dag_id, task_id)
            return
        parse.inject_openlineage(task)
    except operator.passthrough_exceptions():
        raise
    except Exception:
        dag_id, task_id = utils.dag_and_task_ids(task)
        logger.warn_once(("unexpected", dag_id, task_id), "OpenLineage не включён: непредвиденная ошибка cluster policy (%s.%s)", dag_id, task_id, exc_info=True)


def reset_state() -> None:
    """Сбрасывает всё модульное состояние политики.

    Зовётся фикстурой ``_reset_policy_state`` (conftest.py) до и после каждого
    теста: дедупликация warning'ов, кэши конфига, мемо зонда и кэш классов
    исключений переживают границу теста и без сброса смешали бы результаты.
    Единственное место, которое знает обо всех четырёх хранилищах сразу.

    :return: None.
    """
    logger._warned.clear()
    variable._cfg.cache_clear()
    variable._validate_cfg.cache_clear()
    probe._jar_memo.clear()
    operator._passthrough_cache = None


# Реэкспорт утилит: тесты и вызывающий код обращаются к ним через пакет политики.
merge_jars = utils.merge_jars
merge_listeners = utils.merge_listeners
