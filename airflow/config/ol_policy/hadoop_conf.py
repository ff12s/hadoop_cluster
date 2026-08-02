"""Разбор конфигов Hadoop и резолв эндпоинтов WebHDFS."""

from __future__ import annotations

import os
import re
from urllib.parse import urlparse
from xml.etree import ElementTree

# Ограничение глубины раскрытия: цикл `a=${b}; b=${a}` обязан завершиться.
_MAX_DEPTH = 20
_VAR_RE = re.compile(r"\$\{([^}]+)\}")
_DEFAULT_WEBHDFS_HTTP_PORT = 9870
_DEFAULT_WEBHDFS_HTTPS_PORT = 9871


def hadoop_conf_dir() -> str:
    """Каталог конфигов Hadoop из окружения.

    :return: значение ``HADOOP_CONF_DIR`` либо ``YARN_CONF_DIR``, иначе путь по умолчанию.
    """
    return os.environ.get("HADOOP_CONF_DIR") or os.environ.get("YARN_CONF_DIR") or "/etc/hadoop/conf"


def _expand_vars(value: str, props: dict[str, str]) -> str:
    """Раскрывает ссылки ``${name}`` по другим свойствам того же файла.

    Неизвестная переменная и цикл оставляют плейсхолдер, а не роняют разбор.

    :param value: сырое значение свойства.
    :param props: все свойства файла для подстановки.
    :return: значение с раскрытыми ссылками.
    """
    for _ in range(_MAX_DEPTH):
        if "${" not in value:
            return value
        expanded = _VAR_RE.sub(lambda match: props.get(match.group(1), match.group(0)), value)
        if expanded == value:
            return value
        value = expanded
    return value


def parse_hadoop_xml(filename: str) -> dict[str, str]:
    """Разбирает ``*-site.xml`` из каталога конфигов в словарь свойств.

    :param filename: имя файла в ``hadoop_conf_dir()``.
    :return: свойства файла с раскрытыми ``${var}``; свойства без имени или значения пропущены.
    :raises OSError: файл недоступен.
    :raises ElementTree.ParseError: файл не является корректным XML.
    """
    tree = ElementTree.parse(os.path.join(hadoop_conf_dir(), filename))  # noqa: S314 конфиги кластера, не ввод
    props: dict[str, str] = {}
    for prop in tree.getroot().findall("property"):
        name = prop.findtext("name")
        value = prop.findtext("value")
        if name and value is not None:
            props[name] = value
    return {name: _expand_vars(value, props) for name, value in props.items()}


def resolve_webhdfs_urls() -> list[str]:
    """Определяет эндпоинты WebHDFS по конфигам кластера: HA-список, одиночный адрес либо фолбэк.

    :return: список адресов вида ``http://host:port`` без завершающего слэша;
        пустой список, если по конфигам эндпоинты определить нельзя.
    :raises OSError: конфиг кластера недоступен.
    :raises ElementTree.ParseError: конфиг кластера не является корректным XML.
    """
    props = parse_hadoop_xml("hdfs-site.xml")
    is_https = props.get("dfs.http.policy", "").upper() == "HTTPS_ONLY"
    scheme = "https" if is_https else "http"
    addr_key = "dfs.namenode.https-address" if is_https else "dfs.namenode.http-address"

    ha_urls: list[str] = []
    for ns in (n.strip() for n in props.get("dfs.nameservices", "").split(",") if n.strip()):
        for nn in (x.strip() for x in props.get(f"dfs.ha.namenodes.{ns}", "").split(",") if x.strip()):
            addr = props.get(f"{addr_key}.{ns}.{nn}")
            if addr:
                ha_urls.append(f"{scheme}://{addr}")
    if ha_urls:
        return ha_urls

    if props.get(addr_key):
        return [f"{scheme}://{props[addr_key]}"]

    # Фолбэк стенда: http-address не задан, адрес берётся из fs.defaultFS.
    host = urlparse(parse_hadoop_xml("core-site.xml").get("fs.defaultFS", "")).hostname
    if host:
        port = _DEFAULT_WEBHDFS_HTTPS_PORT if is_https else _DEFAULT_WEBHDFS_HTTP_PORT
        return [f"{scheme}://{host}:{port}"]
    return []
