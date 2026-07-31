"""Рендер-фаза: Jinja зовёт ``ol_macro`` на воркере и получает значения лайниджа.

Здесь и только здесь читается Variable и проверяется наличие jar'а в HDFS.
Отказ по любой причине возвращает то, что задал сам DAG, а не пустую строку —
иначе выключенный лайнидж стирал бы чужие listener'ы и jar'ы.
"""

from __future__ import annotations

from typing import Callable

from . import probe
from . import utils
from . import variable
from .logger import logger as log


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


def _emit(value: str, dag_cur: str | None, merge: Callable[[object, object], str], key: str) -> str:
    """Оформляет наше значение под тот канал, которым парс передал DAG-значение.

    Единственное место, где решается разделитель: пустой результат макроса не
    должен оставлять в conf висячую запятую.

    :param value: наше значение из Variable, уже прошедшее ``_clean``.
    :param dag_cur: канал, выбранный парсом: ``""`` — DAG молчал, строка —
        безопасный литерал DAG-значения, ``None`` — текст DAG'а стоит слева.
    :param merge: ``utils.merge_listeners`` либо ``_merge_jars_pair``.
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


def _merge_jars_pair(dag_cur: object, our_jar: object) -> str:
    """Мердж двух источников jar'ов — форма, которую ждёт ``_emit``.

    Третий канал (``conf["spark.jars"]``) склеен с атрибутом ``jars`` ещё на
    парсе, поэтому на рендере источников ровно два.

    :param dag_cur: склеенные на парсе jar'ы DAG'а.
    :param our_jar: URI openlineage-spark jar'а.
    :return: список jar'ов через запятую, без дубликатов, с сохранением порядка.
    """
    return utils.merge_jars(dag_cur, None, our_jar if isinstance(our_jar, str) else "")


def _scalar(value: str, dag_cur: str | None, key: str) -> str:
    """Возвращает скалярное значение lineage-ключа, логируя перебитое DAG-значение.

    Разделителя у скаляра нет: OL побеждает целиком, ключ уже перекрыт на парсе.
    Пустое значение сюда не попадает: ``variable._validate_cfg`` уже отверг такую
    Variable до вызова ``ol_macro``.

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


def _jar_ok(cfg: dict[str, object], *, warn: bool = False) -> bool:
    """Подтверждён ли openlineage-jar в HDFS — общий гейт всего лайниджа.

    Инвариант 19: ``spark.extraListeners`` без jar'а на classpath роняет драйвер
    ``ClassNotFoundException``, поэтому неподтверждённый jar выключает лайнидж
    целиком, а не одну только ветку ``jar``. Зонд мемоизирован по URI, так что
    четыре ветки макроса за один рендер стоят одного похода в сеть, а порядок
    рендера ``conf`` и ``jars`` перестаёт что-либо значить. Пустой
    ``openlineage_jar`` сюда не попадает: ``variable._validate_cfg`` уже отверг
    такую Variable до вызова ``ol_macro``.

    :param cfg: разобранный конфиг из ``_validate_cfg``.
    :param warn: писать ли причину отказа. True только у ветки ``jar``: иначе три
        остальные ветки того же рендера продублировали бы одно сообщение.
    :return: True, если jar подтверждён в HDFS; False при любом отказе.
    """
    jar_uri_obj = cfg.get("openlineage_jar")
    jar_uri = jar_uri_obj.strip() if isinstance(jar_uri_obj, str) else ""
    path = probe.jar_path(jar_uri)
    if path is None:
        if warn:
            log.warning("OpenLineage не включён: openlineage_jar задан без схемы или без пути (%s)", jar_uri)
        return False
    if not probe.jar_available(jar_uri, path):
        if warn:
            log.warning(
                "OpenLineage не включён: jar отсутствует или недоступен в HDFS (%s). "
                "Залейте его: scripts/seed-openlineage-jar.bat",
                jar_uri,
            )
        return False
    return True


def _resolve_jar(cfg: dict[str, object], dag_cur: str | None) -> str:
    """Оформляет URI подтверждённого jar'а под канал DAG-значения.

    Ветка ``jar`` — единственная, которая называет причину отказа зонда: остальные
    три гейтятся тем же ``_jar_ok`` молча, чтобы один отказ не звучал четырежды.

    :param cfg: разобранный конфиг из ``_validate_cfg``.
    :param dag_cur: канал DAG-значения jar'ов, выбранный парсом.
    :return: строка для подстановки в атрибут ``jars``; при любом отказе —
        собственное значение DAG'а (``dag_cur``, когда это строка, иначе ""), а не
        пустая строка: отсутствующий в HDFS jar не должен стирать чужой ``jars=``.
    """
    if not _jar_ok(cfg, warn=True):
        return _refusal(dag_cur)
    jar_uri_obj = cfg.get("openlineage_jar")
    jar_uri = jar_uri_obj.strip() if isinstance(jar_uri_obj, str) else ""
    return _emit(jar_uri, dag_cur, _merge_jars_pair, "spark.jars")


def ol_macro(field: str, forced: bool | None = None, dag_cur: str | None = "") -> str:
    """Рендер-функция: единственный источник значений лайниджа. Зовётся Jinja на воркере.

    Не бросает никогда: битый конфиг обязан давать «лайниджа нет», а не падение
    рендера всей таски. Все четыре ветки гейтятся зондом jar'а (инвариант 19):
    неподтверждённый в HDFS jar выключает лайнидж целиком, иначе listener уехал бы
    в conf без своего класса на classpath и уронил драйвер.

    :param field: "listener", "url", "namespace" либо "jar".
    :param forced: True — DAG форсировал включение, False — форс-выключение, None — форса нет.
    :param dag_cur: канал DAG-значения, выбранный парсом. ``""`` — DAG ключ не задавал,
        строка — безопасный литерал, ``None`` — текст DAG'а стоит слева от вызова.
        Для скаляров ``url`` и ``namespace`` — только материал конфликтного лога.
    :return: значение для подстановки. Если лайнидж выключен или конфиг негоден —
        собственное значение DAG'а (``dag_cur``, когда это строка, иначе ""), а не
        пустая строка: отказ от лайниджа не должен стирать чужой ``spark.jars``
        или ``spark.extraListeners``.
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
    cfg = variable._validate_cfg()
    if cfg is None:
        return _refusal(dag_cur)
    if field == "jar":
        return _resolve_jar(cfg, dag_cur)
    if field not in ("listener", "url", "namespace"):
        log.info("ol_policy: неизвестное поле макроса %s — подстановки нет", field)
        return _refusal(dag_cur)
    # Инвариант 19: нет jar'а — нет и лайниджа. Listener без jar'а на classpath
    # роняет драйвер, то есть отказ зонда обязан гасить все ветки, а не одну.
    if not _jar_ok(cfg):
        return _refusal(dag_cur)
    spark_conf_obj: object = cfg.get("spark_conf", {})
    spark_conf: dict[str, object] = spark_conf_obj if isinstance(spark_conf_obj, dict) else {}
    if field == "listener":
        return _emit(variable._clean(spark_conf.get("spark.extraListeners")), dag_cur, utils.merge_listeners, "spark.extraListeners")
    if field == "url":
        return _scalar(variable._clean(spark_conf.get("spark.openlineage.transport.url"), require_scheme=True),
                       dag_cur, "spark.openlineage.transport.url")
    return _scalar(variable._clean(spark_conf.get("spark.openlineage.namespace")), dag_cur,
                   "spark.openlineage.namespace")
