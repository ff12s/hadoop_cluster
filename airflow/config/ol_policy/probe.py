"""Зонд openlineage-spark jar в HDFS: WebHDFS-опрос под дедлайном, мемо по URI.

Единственное место пакета, которое ходит в сеть, и вызывается оно только на рендере
(почему не на парсе — см. ``parse``).
"""

from __future__ import annotations

import json
import threading
from typing import Literal
from urllib.error import HTTPError
from urllib.parse import quote, urlparse
from urllib.request import urlopen

from . import hadoop_conf, handlers, utils
from .logger import warn_once

# Исход опроса одного эндпоинта WebHDFS.
_Outcome = Literal["found", "absent", "standby", "error"]

# Ограничители зонда: дедлайн на весь перебор, таймаут одного эндпоинта, TTL мемо.
# Модульные, потому что тесты подменяют их monkeypatch'ем.
_PROBE_DEADLINE_SEC = 5.0
ENDPOINT_TIMEOUT_SEC = 2.0
_MEMO_TTL_SEC = 300.0

# Ре-экспорты ради тестов: ``monkeypatch.setattr(probe, ...)`` должен попадать в
# символ, который читает этот модуль, а не в модуль-владелец.
_now = utils.now
resolve_webhdfs_urls = hadoop_conf.resolve_webhdfs_urls

# Мемо зонда: jar_uri -> (available, timestamp). Время — по ``_now``.
_jar_memo: dict[str, tuple[bool, float]] = {}


def jar_path(jar_uri: str) -> str | None:
    """Путь внутри HDFS из значения поля ``openlineage_jar``.

    Схема обязательна: значение без схемы ``spark-submit`` трактует в ``--jars``
    как локальный файл сабмит-хоста, и джоба падает на локализации. Authority
    (RPC-хост и RPC-порт) игнорируется — эндпоинты WebHDFS даёт резолвер.

    :param jar_uri: значение поля ``openlineage_jar``.
    :return: абсолютный путь для WebHDFS либо None, если значение негодно.
    """
    parsed = urlparse(jar_uri.strip())
    if not parsed.scheme or not parsed.path:
        return None
    return parsed.path if parsed.path.startswith("/") else f"/{parsed.path}"


def _is_standby(error: HTTPError) -> bool:
    """Ответил ли standby-NameNode.

    :param error: ответ WebHDFS с кодом 403.
    :return: True, если в теле лежит ``RemoteException.exception == StandbyException``.
    """
    try:
        body = json.loads(error.read().decode("utf-8", "replace"))
    except Exception:
        return False
    remote = body.get("RemoteException") if isinstance(body, dict) else None
    return isinstance(remote, dict) and remote.get("exception") == "StandbyException"


def _query_endpoint(endpoint: str, path: str) -> _Outcome:
    """Спрашивает один эндпоинт WebHDFS про файл.

    :param endpoint: адрес вида ``http://host:port``.
    :param path: абсолютный путь файла в HDFS.
    :return: "found", "absent", "standby" либо "error".
    """
    url = f"{endpoint}/webhdfs/v1{quote(path)}?op=GETFILESTATUS"
    try:
        with urlopen(url, timeout=ENDPOINT_TIMEOUT_SEC) as response:  # noqa: S310 URL строим мы сами
            return "found" if response.status == 200 else "error"
    except HTTPError as error:
        if error.code == 404:
            return "absent"
        if error.code == 403 and _is_standby(error):
            return "standby"
        return "error"
    except Exception:
        return "error"


def _probe(path: str) -> bool:
    """Перебирает эндпоинты WebHDFS до первого осмысленного ответа.

    Standby-NameNode — не отказ, а «спроси активный». Молчаливым остаётся ровно
    один исход: 404, то есть кластер ответил и jar'а действительно нет.

    :param path: абсолютный путь jar'а в HDFS.
    :return: True, если jar есть; False, если его нет либо ни один эндпоинт не ответил.
    :raises handlers.NoEndpointsError: резолвер не дал ни одного эндпоинта.
    """
    endpoints = resolve_webhdfs_urls()
    if not endpoints:
        raise handlers.NoEndpointsError(hadoop_conf.hadoop_conf_dir())
    standby_only = True
    for endpoint in endpoints:
        outcome = _query_endpoint(endpoint, path)
        if outcome == "found":
            return True
        if outcome == "absent":
            return False
        if outcome != "standby":
            standby_only = False
    if standby_only:
        warn_once(("all-standby",), "OpenLineage не включён: все NameNode ответили standby (%s)", path)
    else:
        warn_once(("endpoints-down",), "OpenLineage не включён: эндпоинты WebHDFS недоступны (%s)", path)
    return False


def _probe_worker(path: str, slot: list[bool | BaseException]) -> None:
    """Тело демон-потока зонда: кладёт в слот результат либо исключение.

    :param path: абсолютный путь jar'а в HDFS.
    :param slot: список-слот, куда кладётся ровно один элемент.
    :return: None.
    """
    try:
        slot.append(_probe(path))
    except Exception as error:
        slot.append(error)


def jar_available(jar_uri: str, path: str) -> bool:
    """Лежит ли openlineage-spark jar в HDFS.

    Весь перебор, включая резолв эндпоинтов, уходит в демон-поток: таймаут
    сокета не покрывает ``getaddrinfo``, а зависший вызов на парсе съедает бюджет
    ``[core] dag_file_processor_timeout`` и убивает разбор DAG-файла целиком.
    Результат брошенного потока отбрасывается — мемо пишет ожидающая сторона,
    иначе две таски одного файла получили бы разные ответы.

    Мемо по ``jar_uri`` с TTL ``_MEMO_TTL_SEC``: поток тасок, который ходит за
    jar'ом с предсказуемым путём, не должен перегаживать кластер. Поток, доехавший
    после дедлайна, мемо не переписывает — поздняя запись потеряла бы актуальность.

    :param jar_uri: значение поля ``openlineage_jar``; оно же ключ мемо.
    :param path: разобранный путь jar'а для WebHDFS.
    :return: True, если jar доступен; False во всех остальных исходах.
    """
    cached = _jar_memo.get(jar_uri)
    if cached is not None:
        value, stamped = cached
        if _now() - stamped < _MEMO_TTL_SEC:
            return value
        _jar_memo.pop(jar_uri, None)

    slot: list[bool | BaseException] = []
    worker = threading.Thread(target=_probe_worker, args=(path, slot), daemon=True, name="openlineage-jar-probe")
    worker.start()
    worker.join(_PROBE_DEADLINE_SEC)

    if not slot:
        available = False
        warn_once(
            ("probe-deadline",),
            "OpenLineage не включён: зонд jar не уложился в дедлайн %s с (%s)",
            _PROBE_DEADLINE_SEC,
            jar_uri,
        )
    elif isinstance(slot[0], BaseException):
        available = False
        if isinstance(slot[0], handlers.NoEndpointsError):
            warn_once(
                ("no-endpoints",),
                "OpenLineage не включён: эндпоинты WebHDFS не определены по HADOOP_CONF_DIR (%s)",
                slot[0],
            )
        else:
            warn_once(
                ("probe-error",),
                "OpenLineage не включён: не удалось определить эндпоинты WebHDFS (%s): %s",
                jar_uri,
                slot[0],
            )
    else:
        available = slot[0]

    _jar_memo[jar_uri] = (available, _now())
    return available
