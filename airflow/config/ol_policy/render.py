"""Рендер-фаза: Jinja зовёт ``ol_macro`` на воркере и получает значения лайниджа.

Здесь и только здесь читается Variable и проверяется наличие jar'а в HDFS.
Любой отказ возвращает то, что задал сам DAG, а не пустую строку — см. ``_refusal``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from . import probe, utils, variable
from .logger import logger as log

if TYPE_CHECKING:
    from collections.abc import Callable


def _refusal(dag_cur: str | None) -> str:
    """Значение канала при отказе лайниджа: собственное значение DAG'а не теряется.

    Отказ (форс-выключение, ``enabled: false``, битая или неполная Variable) не
    должен стирать то, что DAG положил в conf/``jars`` сам, по причинам, не
    связанным с лайниджем. Канал ``None`` в этом правиле не участвует: текст
    DAG'а там уже стоит слева от вызова макроса (см. ``_dag_channel``), и
    подставлять его повторно значило бы задвоить.

    :param dag_cur: канал DAG-значения из ``_dag_channel``.
    :return: ``dag_cur``, если это строка; иначе "".
    """
    return dag_cur if isinstance(dag_cur, str) else ""


def _emit(value: str, dag_cur: str | None, merge: Callable[..., str], key: str) -> str:
    """Оформляет наше значение под тот канал, которым парс передал DAG-значение.

    Единственное место, где решается разделитель: пустой результат макроса не
    должен оставлять в conf висячую запятую.

    :param value: наше значение из Variable, уже прошедшее ``_clean``.
    :param dag_cur: канал, выбранный парсом: ``""`` — DAG молчал, строка —
        безопасный литерал DAG-значения, ``None`` — текст DAG'а стоит слева.
    :param merge: ``utils.merge_csv``.
    :param key: имя ключа conf для лога.
    :return: строка для подстановки на месте вызова макроса.
    """
    if dag_cur is None:
        log.info("ol_policy: %s дописан к DAG-значению, дедуп невозможен: %s", key, value)
        return f",{value}"
    if not dag_cur:
        log.info("ol_policy: %s подмешан: %s", key, value)
        return value
    merged = merge(dag_cur, value)
    log.info("ol_policy: %s мердж: %s", key, merged)
    return merged


def _scalar(value: str, dag_cur: str | None, key: str) -> str:
    """Возвращает скалярное значение lineage-ключа, логируя перебитое DAG-значение.

    Разделителя у скаляра нет: OL побеждает целиком, ключ уже перекрыт на парсе.

    :param value: значение из Variable, прошедшее ``_clean``; непустое.
    :param dag_cur: DAG-значение того же ключа либо ``None``, если оно не литерализуемо.
    :param key: имя ключа conf для лога.
    :return: значение из Variable.
    """
    if dag_cur:
        log.info("ol_policy: %s в DAG-conf=%s переопределяется OL-значением=%s", key, dag_cur, value)
    else:
        log.info("ol_policy: %s подмешан: %s", key, value)
    return value


def _jar_ok(config: variable.Config, *, warn: bool = False) -> bool:
    """Подтверждён ли openlineage-jar в HDFS — общий гейт всего лайниджа.

    Инвариант 19: ``spark.extraListeners`` без jar'а на classpath роняет драйвер
    ``ClassNotFoundException``, поэтому неподтверждённый jar выключает лайнидж
    целиком, а не одну только ветку ``jar``. Зонд мемоизирован по URI, так что
    четыре ветки макроса за один рендер стоят одного похода в сеть, а порядок
    рендера ``conf`` и ``jars`` перестаёт что-либо значить.

    :param config: проверенный конфиг из ``variable._validate_cfg``.
    :param warn: писать ли причину отказа. True только у ветки ``jar``: иначе три
        остальные ветки того же рендера продублировали бы одно сообщение.
    :return: True, если jar подтверждён в HDFS; False при любом отказе.
    """
    path = probe.jar_path(config.jar_uri)
    if path is None:
        if warn:
            log.warning("OpenLineage не включён: openlineage_jar задан без схемы или без пути (%s)", config.jar_uri)
        return False
    if not probe.jar_available(config.jar_uri, path):
        if warn:
            log.warning(
                "OpenLineage не включён: jar отсутствует или недоступен в HDFS (%s). "
                "Залейте его: scripts/seed-openlineage-jar.bat",
                config.jar_uri,
            )
        return False
    return True


def _resolve_jar(config: variable.Config, dag_cur: str | None) -> str:
    """Оформляет URI подтверждённого jar'а под канал DAG-значения.

    Ветка ``jar`` — единственная, которая называет причину отказа зонда: остальные
    три гейтятся тем же ``_jar_ok`` молча, чтобы один отказ не звучал четырежды.

    :param config: проверенный конфиг из ``variable._validate_cfg``.
    :param dag_cur: канал DAG-значения jar'ов, выбранный парсом.
    :return: строка для подстановки в атрибут ``jars``; при отказе зонда — значение
        DAG'а по правилу ``_refusal``.
    """
    if not _jar_ok(config, warn=True):
        return _refusal(dag_cur)
    return _emit(config.jar_uri, dag_cur, utils.merge_csv, "spark.jars")


def ol_macro(field: str, forced: bool | None = None, dag_cur: str | None = "") -> str:
    """Рендер-функция: единственный источник значений лайниджа. Зовётся Jinja на воркере.

    Не бросает никогда: битый конфиг обязан давать «лайниджа нет», а не падение
    рендера всей таски. Все четыре ветки гейтятся зондом jar'а — см. ``_jar_ok``.

    :param field: "listener", "url", "namespace" либо "jar".
    :param forced: True — DAG форсировал включение, False — форс-выключение, None — форса нет.
    :param dag_cur: канал DAG-значения, выбранный парсом (разбор каналов — в ``_emit``).
        Для скаляров ``url`` и ``namespace`` — только материал конфликтного лога.
    :return: значение для подстановки; при любом отказе — значение DAG'а по правилу
        ``_refusal``.
    """
    if forced is False:
        log.info("ol_policy: лайнидж выключен форсом DAG-уровня")
        return _refusal(dag_cur)
    cfg = variable._cfg()
    if cfg is None:
        return _refusal(dag_cur)
    enabled = cfg.get("enabled")
    if forced is not True and enabled is not True:
        log.info("ol_policy: лайнидж выключен, Variable.enabled=false и форса DAG'а нет")
        return _refusal(dag_cur)
    config = variable._validate_cfg()
    if config is None:
        return _refusal(dag_cur)
    if field == "jar":
        return _resolve_jar(config, dag_cur)
    if field not in ("listener", "url", "namespace"):
        log.info("ol_policy: неизвестное поле макроса %s — подстановки нет", field)
        return _refusal(dag_cur)
    # Инвариант 19: нет jar'а — нет и лайниджа, отказ зонда гасит все ветки, а не одну.
    if not _jar_ok(config):
        return _refusal(dag_cur)
    if field == "listener":
        return _emit(config.listener, dag_cur, utils.merge_csv, "spark.extraListeners")
    if field == "url":
        return _scalar(config.url, dag_cur, "spark.openlineage.transport.url")
    return _scalar(config.namespace, dag_cur, "spark.openlineage.namespace")
