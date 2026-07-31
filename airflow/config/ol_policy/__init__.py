"""Cluster policy стенда: инъекция OpenLineage в Spark-таски Airflow.

OL-листенер вынесен из общего ``spark-defaults.conf`` (он ломал интерактивный
``spark-shell``), поэтому Airflow навешивает лайнидж своим ``SparkSubmitOperator``
сам — без правок в DAG'ах.

Пакет разложен по фазам жизненного цикла политики: ``parse`` на разборе DAG-файла
дописывает колбэк лайниджа в ``on_execute_callback`` таски, ``callback`` резолвит
значения и пишет conf/jars на воркере перед ``execute()``, ``variable`` читает
Airflow Variable, ``probe`` ходит в HDFS, ``operator`` знает про две раскладки
провайдера. Здесь остаётся только точка входа и общий сброс состояния.

Политика ничего не роняет: любая ошибка гасится и превращается в «лайниджа нет».
Ни один модуль пакета не импортирует Airflow на уровне модуля — импорт идёт внутри
функций, поэтому набор тестов запускается без установленного Airflow.
"""

from __future__ import annotations

# Подмодули импортируются целиком ради путей ``ol_policy.<module>``: подменять
# поведение нужно у модуля-владельца, потому что вызов внутри него идёт через его
# собственный глобал. Символьные реэкспорты ниже — read-only алиасы.
from . import callback, logger, operator, parse, probe, utils, variable  # noqa: F401
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
    """Сбрасывает всё модульное состояние политики (см. reset() модулей).

    Зовётся фикстурой ``_reset_policy_state`` (conftest.py) до и после каждого
    теста: дедупликация warning'ов, кэши конфига, мемо зонда и кэш классов
    исключений переживают границу теста и без сброса смешали бы результаты.

    :return: None.
    """
    logger.reset()
    variable.reset()
    probe.reset()
    operator.reset()


# Реэкспорт утилит: тесты и вызывающий код обращаются к ним через пакет политики.
merge_csv = utils.merge_csv
