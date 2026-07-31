"""Чтение и проверка Airflow Variable ``openlineage_config``.

Читается только на рендере, на воркере (почему не на парсе — см. ``parse``). Никогда
не бросает: при любой ошибке возвращает None, и лайнидж просто не включается.
"""

from __future__ import annotations

import functools
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


@functools.lru_cache(maxsize=1)
def _cfg() -> dict[str, object] | None:
    """Конфиг OL из Airflow Variable. Никогда не бросает: при любой ошибке — None.

    Мемо на процесс: значение читается тремя вызовами макроса за один рендер, а
    процесс запуска таски на воркере живёт одну таску. На исполнителе с
    переиспользуемыми процессами мемо становится кэшем без TTL — правка Variable
    подхватится только следующим процессом.

    :return: разобранный конфиг с ключами enabled, spark_conf, openlineage_jar,
        либо None, если конфиг прочитать не удалось или его форма неверна;
        причина в этом случае уже записана в лог.
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
    # Форма проверяется здесь, содержимое полей — в _validate_cfg: тут решается,
    # тот ли это документ вообще, там — годится ли он для включения лайниджа.
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


@functools.lru_cache(maxsize=1)
def _validate_cfg() -> Config | None:
    """Проверяет годность Variable один раз на процесс: недостающие поля — одним warning'ом.

    Аргументов нет намеренно: под ``lru_cache`` они хэшируются, а разобранный
    конфиг — dict, и любой вызов упал бы с ``TypeError: unhashable type``.

    :return: проверенный конфиг либо None, если он непригоден для включения лайниджа.
    """
    cfg = _cfg()
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
