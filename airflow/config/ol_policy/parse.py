"""Парс-фаза: политика собирает строки и регистрирует макрос, ничего не резолвя.

Ноль обращений к Variable, метастору и HDFS: парс идёт внутри DAG-file-processor'а
шедулера, где зависший вызов съедает бюджет разбора всего файла. Значения приезжают
на рендере — см. ``render``.
"""

from __future__ import annotations

from typing import Literal

from . import operator
from . import utils
from .logger import warn_once

MACRO = "__openlineage_v1"

# Значение с этими фрагментами нельзя вложить литералом в текст вызова макроса:
# Jinja порвётся на вложенных скобках, кавычка — на самой кавычке, а обратный
# слэш Jinja развернёт как escape внутри строкового литерала ("C:\new.jar"
# приедет как "C:" + перевод строки + "ew.jar").
_UNSAFE_FOR_LITERAL = ("{{", "{%", "'", '"', "\\")


def _dag_channel(value: object) -> tuple[str, str | None]:
    """Выбирает канал, которым DAG-значение доедет до макроса.

    Каналов три, и решение принимает парс — единственный, кто видит исходное
    значение: на рендере прочитать его нечем.

    :param value: значение ключа conf либо атрибута оператора, как его задал DAG.
    :return: пара ``(prefix, dag_cur)``. ``prefix`` ставится в строку перед вызовом
        макроса, ``dag_cur`` уходит третьим аргументом макроса.
    """
    text = value.strip() if isinstance(value, str) else ""
    if not text:
        return "", ""
    if any(marker in text for marker in _UNSAFE_FOR_LITERAL):
        return text, None
    return "", text


def _macro_call(field: str, forced: Literal["true", "none"], dag_cur: str | None) -> str:
    """Собирает текст вызова макроса для подстановки в conf.

    :param field: имя ветки макроса.
    :param forced: ``"true"`` либо ``"none"`` — Jinja-литерал форса.
    :param dag_cur: канал DAG-значения из ``_dag_channel``.
    :return: строка вида ``{{ __openlineage_v1('field', none, 'value') }}``.
    """
    literal = "none" if dag_cur is None else f"'{dag_cur}'"
    return f"{{{{ {MACRO}('{field}', {forced}, {literal}) }}}}"


def inject_openlineage(task: object) -> None:
    """Навешивает OpenLineage на проверенную Spark-таску: макрос и строки в conf.

    Порядок гейтов нормативен: форс-выключение проверяется раньше всего, поэтому
    выключивший лайнидж DAG уходит нетронутым. Значения лайниджа сюда не попадают —
    на парсе собираются только строки с вызовами макроса, а Variable и HDFS
    читаются на рендере.

    Порядок двух записей тоже нормативен: атрибут ``jars`` пишется раньше conf,
    поэтому обрыв между ними оставляет таску максимум с лишним jar'ом на classpath,
    но без листенера — то есть без лайниджа, что безопасно; в обратном порядке
    обрыв оставил бы листенер без jar'а, а это уже сломанный запуск Spark.

    :param task: экземпляр ``SparkSubmitOperator``; мутируется на месте.
    :return: None.
    """
    # Импорт внутри функции, а не на уровне модуля: from . import render потянул бы
    # за собой variable и probe, а парс не должен доставать до Variable и сети.
    from . import render
    macro = render.ol_macro

    dag_id, task_id = utils.dag_and_task_ids(task)

    attrs = operator.operator_attrs(task)
    if attrs is None:
        warn_once(("unknown-layout", dag_id, task_id), "OpenLineage не включён: незнакомая раскладка атрибутов оператора (%s.%s)", dag_id, task_id)
        return

    forced = operator.lineage_forced(task)
    if forced is False:
        return

    dag = utils.task_dag(task)
    if dag is None:
        warn_once(("no-dag", dag_id, task_id), "OpenLineage не включён: таска не привязана к DAG, макрос положить некуда (%s)", task_id)
        return

    # Макрос кладётся в DAG политикой намеренно: это единственный способ отложить
    # чтение Variable до рендера таски, ничего не требуя от автора DAG'а. Чужим
    # считается только объект, который не является нашей функцией, — иначе вторая
    # таска файла увидела бы чужим то, что положила первая.
    macros = dict(getattr(dag, "user_defined_macros", None) or {})
    if MACRO in macros and macros[MACRO] is not macro:
        warn_once(("macro-taken", dag_id, task_id), "OpenLineage не включён: имя макроса %s занято чужим объектом (%s.%s)", MACRO, dag_id, task_id)
        return

    forced_literal: Literal["true", "none"] = "true" if forced is True else "none"
    cur_conf = dict(getattr(task, attrs.conf) or {})

    listener_prefix, listener_cur = _dag_channel(cur_conf.get("spark.extraListeners"))
    # Оба канала jar'ов известны здесь и склеиваются до макроса: на рендере
    # прочитать их будет нечем.
    jars_prefix, jars_cur = _dag_channel(utils.merge_jars(getattr(task, attrs.jars), cur_conf.get("spark.jars"), ""))
    # Для скаляров префикс отбрасывается: OL побеждает целиком, дописывать текст
    # DAG'а слева значило бы нарушить это правило.
    _, url_cur = _dag_channel(cur_conf.get("spark.openlineage.transport.url"))
    _, namespace_cur = _dag_channel(cur_conf.get("spark.openlineage.namespace"))

    if listener_cur is None or jars_cur is None:
        warn_once(
            ("jinja-channel", dag_id, task_id),
            "OpenLineage: DAG-значение содержит Jinja — дедуп значения OL невозможен (%s.%s)",
            dag_id,
            task_id,
        )

    macros[MACRO] = macro
    dag.user_defined_macros = macros
    setattr(task, attrs.jars, jars_prefix + _macro_call("jar", forced_literal, jars_cur))
    setattr(task, attrs.conf, {
        **cur_conf,
        "spark.extraListeners": listener_prefix + _macro_call("listener", forced_literal, listener_cur),
        "spark.openlineage.transport.type": "http",
        "spark.openlineage.transport.url": _macro_call("url", forced_literal, url_cur),
        "spark.openlineage.namespace": _macro_call("namespace", forced_literal, namespace_cur),
        "spark.openlineage.columnLineage.datasetLineageEnabled": "true",
    })
