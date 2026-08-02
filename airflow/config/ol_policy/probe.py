"""Зонд наличия openlineage-spark jar в HDFS через WebHDFS."""

from __future__ import annotations

import base64
import json
import threading
import time
from typing import Literal
from urllib.error import HTTPError
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen

from . import hadoop_conf, handlers
from .logger import warn_once

_EndpointOutcome = Literal["found", "absent", "standby", "error"]
_ProbeOutcome = Literal["found", "absent", "down"]

_PROBE_DEADLINE_SEC = 17.0
ENDPOINT_TIMEOUT_SEC = 2.0
_RETRY_PAUSE_SEC = 0.5

# Реэкспорт ради monkeypatch: тесты подменяют символ, который читает этот модуль.
resolve_webhdfs_urls = hadoop_conf.resolve_webhdfs_urls
_sleep = time.sleep


def jar_path(jar_uri: str) -> str | None:
    """Возвращает абсолютный путь внутри HDFS из значения поля ``openlineage_jar``.

    :param jar_uri: значение поля ``openlineage_jar``.
    :return: абсолютный путь для WebHDFS либо None, если значение без схемы или без пути.
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


def _spnego_header(endpoint: str) -> str | None:
    """Строит SPNEGO-заголовок Authorization для эндпоинта.

    :param endpoint: адрес вида ``http://host:port``.
    :return: строка ``Negotiate <base64>`` либо None, если токен получить не удалось.
    """
    host = urlparse(endpoint).hostname
    if not host:
        return None
    try:
        import spnego

        token = spnego.client(hostname=host, service="HTTP", protocol="kerberos").step()
    except Exception:
        return None
    if not token:
        return None
    return "Negotiate " + base64.b64encode(token).decode("ascii")


def _query_with_auth(endpoint: str, url: str) -> _EndpointOutcome:
    """Повторяет запрос зонда с SPNEGO-заголовком после challenge 401.

    :param endpoint: адрес эндпоинта — источник hostname для токена.
    :param url: полный URL первоначального запроса.
    :return: "found", "absent", "standby" либо "error".
    """
    header = _spnego_header(endpoint)
    if header is None:
        warn_once(
            ("kerberos-unavailable",),
            "OpenLineage не включён: WebHDFS требует Kerberos (401), SPNEGO-токен получить не удалось",
        )
        return "error"
    request = Request(url, headers={"Authorization": header})
    try:
        with urlopen(request, timeout=ENDPOINT_TIMEOUT_SEC) as response:  # noqa: S310 URL строим мы сами
            return "found" if response.status == 200 else "error"
    except HTTPError as error:
        if error.code == 404:
            return "absent"
        if error.code == 403 and _is_standby(error):
            return "standby"
        return "error"
    except Exception:
        return "error"


def _query_endpoint(endpoint: str, path: str) -> _EndpointOutcome:
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
        if error.code == 401:
            return _query_with_auth(endpoint, url)
        return "error"
    except Exception:
        return "error"


def _probe(path: str) -> _ProbeOutcome:
    """Опрашивает эндпоинты WebHDFS, при сплошных неавторитетных ответах — второй проход.

    :param path: абсолютный путь jar'а в HDFS.
    :return: "found", "absent" либо "down" — ни один эндпоинт не ответил авторитетно.
    :raises handlers.NoEndpointsError: резолвер не дал ни одного эндпоинта.
    """
    endpoints = resolve_webhdfs_urls()
    if not endpoints:
        raise handlers.NoEndpointsError(hadoop_conf.hadoop_conf_dir())
    standby_only = True
    for attempt in range(2):
        if attempt:
            _sleep(_RETRY_PAUSE_SEC)
        for endpoint in endpoints:
            outcome = _query_endpoint(endpoint, path)
            if outcome == "found":
                return "found"
            if outcome == "absent":
                return "absent"
            if outcome != "standby":
                standby_only = False
    if standby_only:
        warn_once(("all-standby",), "OpenLineage не включён: все NameNode ответили standby (%s)", path)
    else:
        warn_once(("endpoints-down",), "OpenLineage не включён: эндпоинты WebHDFS недоступны (%s)", path)
    return "down"


def _probe_worker(path: str, slot: list[_ProbeOutcome | BaseException]) -> None:
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
    """Проверяет наличие openlineage-spark jar в HDFS, прерываясь по дедлайну.

    :param jar_uri: значение поля ``openlineage_jar`` — для текстов warning'ов.
    :param path: разобранный путь jar'а для WebHDFS.
    :return: True, если jar доступен; False во всех остальных исходах.
    """
    slot: list[_ProbeOutcome | BaseException] = []
    worker = threading.Thread(target=_probe_worker, args=(path, slot), daemon=True, name="openlineage-jar-probe")
    worker.start()
    worker.join(_PROBE_DEADLINE_SEC)

    if not slot:
        warn_once(
            ("probe-deadline",),
            "OpenLineage не включён: зонд jar не уложился в дедлайн %s с (%s)",
            _PROBE_DEADLINE_SEC,
            jar_uri,
        )
        return False
    outcome = slot[0]
    if isinstance(outcome, BaseException):
        if isinstance(outcome, handlers.NoEndpointsError):
            warn_once(
                ("no-endpoints",),
                "OpenLineage не включён: эндпоинты WebHDFS не определены по HADOOP_CONF_DIR (%s)",
                outcome,
            )
        else:
            warn_once(
                ("probe-error",),
                "OpenLineage не включён: не удалось определить эндпоинты WebHDFS (%s): %s",
                jar_uri,
                outcome,
            )
        return False
    return outcome == "found"
