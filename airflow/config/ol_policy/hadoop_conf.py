"""Разбор конфигов Hadoop и резолв WebHDFS-эндпоинтов для cluster policy.

Перенос из ``SparkAPI/app/core/hadoop_api``: разбор ``*-site.xml`` с раскрытием
``${var}`` и резолв адресов NameNode (``HdfsApi._resolve_urls``). Только
стандартная библиотека: ``defusedxml`` есть не во всех целевых средах, а
разбираются собственные файлы кластера, смонтированные на чтение.

Кэш разбора по mtime не переносится: конфиги запечены в образ, а процесс, в
котором работает политика, живёт один парс DAG-файла — инвалидировать нечего.
Модуль не импортирует Airflow: он же используется тестами без него.
"""

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


def _expand(value: str, props: dict[str, str]) -> str:
    """Раскрывает ``${name}`` по другим свойствам того же файла.

    Повторяет property-only подстановку ``Configuration.get()``: неизвестная
    переменная и цикл оставляют плейсхолдер, а не роняют разбор. Формы
    ``${env.VAR}`` и ``${system.prop}`` не поддерживаются — конфиги кластера их
    не используют.

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

    Свойства без имени или без значения пропускаются.

    :param filename: имя файла в ``hadoop_conf_dir()``.
    :return: свойства файла с раскрытыми ``${var}``.
    :raises OSError: файл недоступен.
    :raises ElementTree.ParseError: файл не является корректным XML.
    """
    tree = ElementTree.parse(os.path.join(hadoop_conf_dir(), filename))
    props: dict[str, str] = {}
    for prop in tree.getroot().findall("property"):
        name = prop.findtext("name")
        value = prop.findtext("value")
        if name and value is not None:
            props[name] = value
    return {name: _expand(value, props) for name, value in props.items()}


def resolve_webhdfs_urls() -> list[str]:
    """Эндпоинты WebHDFS по конфигам кластера: HA, одиночный адрес либо фолбэк.

    Порядок повторяет оригинал: ``dfs.http.policy`` задаёт схему и ключ адреса,
    затем HA-список по ``dfs.nameservices``, затем одиночный адрес, затем хост из
    ``fs.defaultFS`` с портом WebHDFS по умолчанию.

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
