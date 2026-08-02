"""Чтение и проверка Airflow Variable ``openlineage_config``.

Читается только из колбэка, на воркере (почему не на парсе — см. ``parse``). Никогда
не бросает: при любой ошибке возвращает None, и лайнидж просто не включается.
"""

from __future__ import annotations

import json
from typing import NamedTuple

from . import utils
from .logger import warn_once

VARIABLE = "openlineage_config"

_TTL_SEC = 300.0

# Ре-экспорт ради тестов: фикстура ``clock`` подменяет символ, который читает
# этот модуль, а не модуль-владелец (тот же приём, что в probe).
_now = utils.now

# Мемо на процесс с TTL: значение читается несколько раз за один запуск таски,
# а на исполнителе с переиспользуемыми процессами правка Variable подхватится
# не позже чем через _TTL_SEC.
_cfg_memo: tuple[float, dict[str, object] | None] | None = None
_validated_memo: tuple[float, Config | None] | None = None


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


def _cfg_with_stamp() -> tuple[float, dict[str, object] | None]:
    """Конфиг OL из Airflow Variable вместе со штампом мемо, с TTL-мемо на процесс.

    Общая точка правды для ``_cfg`` и ``_validate_cfg``: обеим нужен один и тот же
    штамп свежести, иначе валидированный конфиг мог бы протухать не в такт с сырым
    (см. ``_validate_cfg``).

    Колбэк читает конфиг несколько раз за один запуск таски (гейт ``enabled``,
    затем валидированные значения) — TTL защищает от повторного похода в
    metastore внутри одного и того же запуска. На исполнителе с переиспользуемыми
    процессами правка Variable подхватится не позже чем через ``_TTL_SEC``.

    :return: пара (штамп мемо, конфиг); конфиг — словарь с ключами enabled,
        spark_conf, openlineage_jar, либо None, если его не удалось прочитать
        или его форма неверна; причина в этом случае уже записана в лог.
    """
    global _cfg_memo
    if _cfg_memo is not None and _now() - _cfg_memo[0] < _TTL_SEC:
        return _cfg_memo
    value = _load_cfg()
    _cfg_memo = (_now(), value)
    return _cfg_memo


def _cfg() -> dict[str, object] | None:
    """Конфиг OL из Airflow Variable, с TTL-мемо на процесс.

    :return: разобранный конфиг с ключами enabled, spark_conf, openlineage_jar,
        либо None, если конфиг прочитать не удалось или его форма неверна;
        причина в этом случае уже записана в лог.
    """
    return _cfg_with_stamp()[1]


def _load_cfg() -> dict[str, object] | None:
    """Читает и разбирает Variable ``openlineage_config``. Никогда не бросает.

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


def _validate_cfg() -> Config | None:
    """Проверяет годность Variable, с мемо на процесс: недостающие поля — одним warning'ом.

    Свежесть не считается отдельным TTL, а завязана на мемо ``_cfg``: валидированное
    значение пересчитывается ровно тогда, когда обновляется сырое, — иначе валидированный
    конфиг мог бы протухнуть позже сырого и отдавать старые url/namespace/jar ещё
    до ``_TTL_SEC`` после его перезагрузки.

    :return: проверенный конфиг либо None, если он непригоден для включения лайниджа.
    """
    global _validated_memo
    stamp, cfg = _cfg_with_stamp()
    if _validated_memo is not None and _validated_memo[0] == stamp:
        return _validated_memo[1]
    value = _validate(cfg)
    _validated_memo = (stamp, value)
    return value


def _validate(cfg: dict[str, object] | None) -> Config | None:
    """Проверяет годность разобранного конфига: недостающие поля — одним warning'ом.

    :param cfg: конфиг, разобранный ``_cfg``, либо None.
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


def reset() -> None:
    """Сбрасывает мемо конфига — для изоляции тестов.

    :return: None.
    """
    global _cfg_memo, _validated_memo
    _cfg_memo = None
    _validated_memo = None
