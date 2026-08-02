"""Колбэк-фаза: резолв значений лайниджа и запись conf/jars на воркере до ``execute()``."""

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
    """Проверяет гейты лайниджа и пишет его значения в таску.

    :param task: execution-копия оператора из контекста.
    :return: None.
    """
    operator_cls = operator._spark_submit_operator()
    if operator_cls is None or not isinstance(task, operator_cls):
        return
    attrs = operator.operator_attrs(task)
    if attrs is None:
        return
    forced = operator.lineage_forced(task)
    if forced is False:
        log.info("ol_policy: лайнидж выключен форсом DAG-уровня")
        return
    cfg = variable.read_config()
    if cfg is None:
        return
    if forced is not True and cfg.get("enabled") is not True:
        log.info("ol_policy: лайнидж выключен, Variable.enabled=false и форса DAG'а нет")
        return
    config = variable.validate_config(cfg)
    if config is None:
        return
    for jar_uri in config.jar_uris:
        path = probe.jar_path(jar_uri)
        if path is None:
            warn_once(
                ("jar-malformed", jar_uri),
                "OpenLineage не включён: openlineage_jar задан без схемы или без пути (%s)",
                jar_uri,
            )
            return
        if not probe.jar_available(jar_uri, path):
            warn_once(
                ("jar-missing", jar_uri),
                "OpenLineage не включён: jar отсутствует или недоступен в HDFS (%s). "
                "Залейте его: scripts/seed-openlineage-jar.bat",
                jar_uri,
            )
            return
    _write_lineage(task, attrs, config)


def _write_lineage(task: object, attrs: operator.OperatorAttrs, config: variable.Config) -> None:
    """Пишет значения лайниджа в атрибуты jars и conf таски; строковый ``spark.jars`` из conf переезжает в jars.

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
    )
    for key, ours in (*overrides, *config.extra_conf.items()):
        dag_value = cur_conf.get(key)
        if isinstance(dag_value, str) and dag_value and dag_value != ours:
            log.info("ol_policy: %s в DAG-conf=%s переопределяется OL-значением=%s", key, dag_value, ours)
    dag_conf_jars = cur_conf.pop("spark.jars", None) if isinstance(cur_conf.get("spark.jars"), str) else None
    # jars пишется раньше conf: обрыв между setattr'ами оставит лишний jar, но не листенер без jar'а.
    setattr(task, attrs.jars, utils.merge_csv(getattr(task, attrs.jars), dag_conf_jars, *config.jar_uris))
    merged_listeners = utils.merge_csv(cur_conf.get("spark.extraListeners"), config.listener)
    log.info("ol_policy: spark.extraListeners=%s", merged_listeners)
    setattr(task, attrs.conf, {
        **cur_conf,
        **config.extra_conf,
        "spark.extraListeners": merged_listeners,
        **dict(overrides),
    })
