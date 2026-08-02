"""Чтение и проверка Airflow Variable ``openlineage_config``."""

from __future__ import annotations

import json
from typing import NamedTuple

from .logger import warn_once

VARIABLE = "openlineage_config"


class Config(NamedTuple):
    """Проверенные поля Variable ``openlineage_config``: все непустые, уже очищенные."""

    listener: str
    url: str
    namespace: str
    jar_uri: str


def _clean(value: object, *, require_scheme: bool = False) -> str:
    """Годное значение поля конфига либо пустая строка.

    :param value: сырое значение из Variable.
    :param require_scheme: требовать префикс ``http://`` или ``https://``.
    :return: значение без окружающих пробелов, либо "", если оно негодно.
    """
    if not isinstance(value, str):
        return ""
    cleaned = value.strip()
    if require_scheme and not cleaned.startswith(("http://", "https://")):
        return ""
    return cleaned


def read_config() -> dict[str, object] | None:
    """Читает Variable ``openlineage_config`` и разбирает её JSON.

    :return: конфиг с ключами enabled, spark_conf, openlineage_jar; None, если прочитать
        не удалось или форма неверна — причина записана в лог.
    """
    try:
        from airflow.models import Variable

        raw = Variable.get(VARIABLE, default_var=None)
    except Exception:
        warn_once(("var-unavailable",), "OpenLineage выключен: Variable openlineage_config недоступна")
        return None
    if not raw:
        warn_once(("no-var",), "OpenLineage выключен: Variable openlineage_config не задана")
        return None
    try:
        parsed = json.loads(raw)
    except Exception:
        warn_once(("bad-json",), "OpenLineage выключен: Variable openlineage_config — не разбираемый JSON")
        return None
    if not isinstance(parsed, dict):
        warn_once(("not-object",), "OpenLineage выключен: Variable openlineage_config — не JSON-объект")
        return None
    if "auth" in parsed:
        warn_once(("auth",), "OpenLineage: ключ 'auth' в Variable не поддерживается и не подставляется")
    shape_ok = (
        isinstance(parsed.get("enabled"), bool)
        and isinstance(parsed.get("spark_conf"), dict)
        and isinstance(parsed.get("openlineage_jar"), str)
    )
    if not shape_ok:
        warn_once(
            ("bad-shape",),
            "OpenLineage выключен: Variable openlineage_config должна иметь ключи "
            "enabled (bool), spark_conf (object), openlineage_jar (str)",
        )
        return None
    return parsed


def validate_config(cfg: dict[str, object] | None) -> Config | None:
    """Проверяет годность разобранного конфига, сообщая о недостающих полях одним warning'ом.

    :param cfg: конфиг, разобранный ``read_config``, либо None.
    :return: проверенный конфиг либо None, если он непригоден для включения лайниджа.
    """
    if cfg is None:
        return None
    spark_conf_obj: object = cfg.get("spark_conf", {})
    spark_conf: dict[str, object] = spark_conf_obj if isinstance(spark_conf_obj, dict) else {}
    config = Config(
        listener=_clean(spark_conf.get("spark.extraListeners")),
        url=_clean(spark_conf.get("spark.openlineage.transport.url"), require_scheme=True),
        namespace=_clean(spark_conf.get("spark.openlineage.namespace")),
        jar_uri=_clean(cfg.get("openlineage_jar")),
    )
    missing = [
        name
        for value, name in (
            (config.listener, "spark_conf.spark.extraListeners (непустая строка)"),
            (config.url, "spark_conf.spark.openlineage.transport.url (http/https URL)"),
            (config.namespace, "spark_conf.spark.openlineage.namespace (непустая строка)"),
            (config.jar_uri, "openlineage_jar (hdfs://... URI)"),
        )
        if not value
    ]
    if missing:
        warn_once(
            ("var-incomplete",),
            "OpenLineage не включён: Variable openlineage_config неполна: %s",
            ", ".join(missing),
        )
        return None
    return config
