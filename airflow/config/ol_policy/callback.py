"""Колбэк-фаза: Airflow зовёт ``ol_execute_callback`` на воркере до ``execute()``.

Единственное место, где читается Variable, зондируется jar и пишутся conf/jars.
Выполняется после рендера шаблонов (значения таски — финальные строки) и до
``execute()``: SparkSubmitOperator читает conf и jars лениво при построении
hook'а, поэтому запись отсюда доезжает до команды spark-submit.

Отказ любого гейта оставляет таску байт-в-байт нетронутой. Колбэк не бросает:
Airflow и сам глотает исключения execute-колбэков, но собственный перехват даёт
наш формат warning'а и дедупликацию.
"""

from __future__ import annotations

from collections.abc import Mapping

from . import operator, probe, utils, variable
from .logger import logger as log, warn_once


def ol_execute_callback(context: Mapping[str, object]) -> None:
    """Точка входа колбэка: достаёт таску из контекста и запускает инъекцию.

    :param context: контекст исполнения Airflow; читается только ключ ``task``.
    :return: None.
    """
    task: object | None = None
    try:
        if isinstance(context, Mapping):
            task = context.get("task")
        if task is None:
            return
        _inject(task)
    except Exception:
        dag_id, task_id = utils.dag_and_task_ids(task)
        warn_once(
            ("callback-unexpected", dag_id, task_id),
            "OpenLineage не включён: непредвиденная ошибка колбэка (%s.%s)",
            dag_id,
            task_id,
            exc_info=True,
        )


def _inject(task: object) -> None:
    """Гейты и запись лайниджа; любой отказ — молчаливый (причины пишут сами гейты).

    :param task: execution-копия оператора из контекста.
    :return: None.
    """
    operator_cls = operator._spark_submit_operator()
    if operator_cls is None or not isinstance(task, operator_cls):
        return
    attrs = operator.operator_attrs(task)
    if attrs is None:
        return  # причина уже названа парс-фазой
    forced = operator.lineage_forced(task)
    if forced is False:
        log.info("ol_policy: лайнидж выключен форсом DAG-уровня")
        return
    cfg = variable._cfg()
    if cfg is None:
        return
    if forced is not True and cfg.get("enabled") is not True:
        log.info("ol_policy: лайнидж выключен, Variable.enabled=false и форса DAG'а нет")
        return
    config = variable._validate(cfg)
    if config is None:
        return
    path = probe.jar_path(config.jar_uri)
    if path is None:
        warn_once(
            ("jar-malformed",),
            "OpenLineage не включён: openlineage_jar задан без схемы или без пути (%s)",
            config.jar_uri,
        )
        return
    if not probe.jar_available(config.jar_uri, path):
        warn_once(
            ("jar-missing",),
            "OpenLineage не включён: jar отсутствует или недоступен в HDFS (%s). "
            "Залейте его: scripts/seed-openlineage-jar.bat",
            config.jar_uri,
        )
        return
    _write(task, attrs, config)


def _write(task: object, attrs: operator.OperatorAttrs, config: variable.Config) -> None:
    """Пишет лайнидж в таску: сначала атрибут jars, затем conf.

    Порядок записи — инвариант: обрыв между setattr'ами оставляет максимум
    лишний jar без листенера (безопасно), но не листенер без jar'а.

    Строковый ключ ``spark.jars`` из итогового conf удаляется: его элементы
    уезжают в атрибут jars (``--jars``), а двойное объявление списка полагалось
    бы на приоритет ``--jars`` у spark-submit. Нестроковое значение (мусор для
    CSV-мерджа) остаётся в conf как было.

    :param task: execution-копия оператора.
    :param attrs: имена атрибутов conf/jars текущей раскладки.
    :param config: проверенный конфиг из Variable.
    :return: None.
    """
    conf_obj = getattr(task, attrs.conf)
    cur_conf: dict[str, object] = dict(conf_obj) if isinstance(conf_obj, dict) else {}
    overrides = (
        ("spark.openlineage.transport.type", "http"),
        ("spark.openlineage.transport.url", config.url),
        ("spark.openlineage.namespace", config.namespace),
        ("spark.openlineage.columnLineage.datasetLineageEnabled", "true"),
    )
    for key, ours in overrides:
        dag_value = cur_conf.get(key)
        if isinstance(dag_value, str) and dag_value and dag_value != ours:
            log.info("ol_policy: %s в DAG-conf=%s переопределяется OL-значением=%s", key, dag_value, ours)
    dag_conf_jars = cur_conf.pop("spark.jars", None) if isinstance(cur_conf.get("spark.jars"), str) else None
    setattr(task, attrs.jars, utils.merge_csv(getattr(task, attrs.jars), dag_conf_jars, config.jar_uri))
    merged_listeners = utils.merge_csv(cur_conf.get("spark.extraListeners"), config.listener)
    log.info("ol_policy: spark.extraListeners=%s", merged_listeners)
    setattr(task, attrs.conf, {
        **cur_conf,
        "spark.extraListeners": merged_listeners,
        **dict(overrides),
    })
