"""Тесты разбора конфигов Hadoop и резолва WebHDFS-эндпоинтов (§6 спеки)."""

from __future__ import annotations

import pathlib

import pytest

from ol_policy import hadoop_conf


def _write_xml(directory: pathlib.Path, filename: str, props: dict[str, str]) -> None:
    """Пишет минимальный ``*-site.xml`` с перечисленными свойствами.

    :param directory: каталог конфигов.
    :param filename: имя файла.
    :param props: свойства ``{name: value}``.
    :return: None.
    """
    body = "".join(f"<property><name>{name}</name><value>{value}</value></property>" for name, value in props.items())
    (directory / filename).write_text(f"<configuration>{body}</configuration>", encoding="utf-8")


@pytest.fixture
def conf_dir(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> pathlib.Path:
    """Пустой ``HADOOP_CONF_DIR`` во временном каталоге.

    :param tmp_path: временный каталог теста.
    :param monkeypatch: фикстура подмены.
    :return: путь каталога конфигов.
    """
    monkeypatch.setenv("HADOOP_CONF_DIR", str(tmp_path))
    return tmp_path


def test_parse_reads_properties(conf_dir: pathlib.Path) -> None:
    """Разбор возвращает свойства файла как есть."""
    _write_xml(conf_dir, "core-site.xml", {"fs.defaultFS": "hdfs://namenode:9000"})

    assert hadoop_conf.parse_hadoop_xml("core-site.xml") == {"fs.defaultFS": "hdfs://namenode:9000"}


def test_parse_expands_variables(conf_dir: pathlib.Path) -> None:
    """``${var}`` раскрывается по другим свойствам того же файла."""
    _write_xml(conf_dir, "hdfs-site.xml", {"base": "/data", "dfs.namenode.name.dir": "${base}/name"})

    assert hadoop_conf.parse_hadoop_xml("hdfs-site.xml")["dfs.namenode.name.dir"] == "/data/name"


def test_parse_leaves_unknown_variable_in_place(conf_dir: pathlib.Path) -> None:
    """Неизвестная переменная остаётся плейсхолдером, а не роняет разбор."""
    _write_xml(conf_dir, "hdfs-site.xml", {"dfs.namenode.name.dir": "${nowhere}/name"})

    assert hadoop_conf.parse_hadoop_xml("hdfs-site.xml")["dfs.namenode.name.dir"] == "${nowhere}/name"


def test_parse_terminates_on_cycle(conf_dir: pathlib.Path) -> None:
    """Цикл ``a=${b}; b=${a}`` завершается, а не зацикливается."""
    _write_xml(conf_dir, "hdfs-site.xml", {"a": "${b}", "b": "${a}"})

    assert hadoop_conf.parse_hadoop_xml("hdfs-site.xml")["a"].startswith("${")


def test_parse_skips_properties_without_name_or_value(conf_dir: pathlib.Path) -> None:
    """Свойства без имени или значения пропускаются."""
    (conf_dir / "hdfs-site.xml").write_text(
        "<configuration>"
        "<property><name>ok</name><value>1</value></property>"
        "<property><value>2</value></property>"
        "<property><name>empty</name></property>"
        "</configuration>",
        encoding="utf-8",
    )

    assert hadoop_conf.parse_hadoop_xml("hdfs-site.xml") == {"ok": "1"}


def test_parse_raises_on_missing_file(conf_dir: pathlib.Path) -> None:
    """Отсутствующий файл — исключение: ловит его вызывающая сторона зонда."""
    with pytest.raises(OSError):
        hadoop_conf.parse_hadoop_xml("hdfs-site.xml")


def test_resolve_ha_endpoints(conf_dir: pathlib.Path) -> None:
    """HA: эндпоинты собираются по ``dfs.nameservices`` и ``dfs.ha.namenodes.*``."""
    _write_xml(
        conf_dir,
        "hdfs-site.xml",
        {
            "dfs.nameservices": "cluster",
            "dfs.ha.namenodes.cluster": "nn1,nn2",
            "dfs.namenode.http-address.cluster.nn1": "nn1host:9870",
            "dfs.namenode.http-address.cluster.nn2": "nn2host:9870",
        },
    )

    assert hadoop_conf.resolve_webhdfs_urls() == ["http://nn1host:9870", "http://nn2host:9870"]


def test_resolve_single_http_address(conf_dir: pathlib.Path) -> None:
    """Не-HA: одиночный ``dfs.namenode.http-address``."""
    _write_xml(conf_dir, "hdfs-site.xml", {"dfs.namenode.http-address": "namenode:9871"})

    assert hadoop_conf.resolve_webhdfs_urls() == ["http://namenode:9871"]


def test_resolve_falls_back_to_default_fs(conf_dir: pathlib.Path) -> None:
    """Фолбэк стенда: хост из ``fs.defaultFS`` плюс порт 9870."""
    _write_xml(conf_dir, "hdfs-site.xml", {"dfs.replication": "1"})
    _write_xml(conf_dir, "core-site.xml", {"fs.defaultFS": "hdfs://namenode:9000"})

    assert hadoop_conf.resolve_webhdfs_urls() == ["http://namenode:9870"]


def test_resolve_https_only(conf_dir: pathlib.Path) -> None:
    """``HTTPS_ONLY``: схема https, ключ адреса https, фолбэчный порт 9871."""
    _write_xml(conf_dir, "hdfs-site.xml", {"dfs.http.policy": "HTTPS_ONLY"})
    _write_xml(conf_dir, "core-site.xml", {"fs.defaultFS": "hdfs://namenode:9000"})

    assert hadoop_conf.resolve_webhdfs_urls() == ["https://namenode:9871"]


def test_resolve_https_only_uses_https_address(conf_dir: pathlib.Path) -> None:
    """``HTTPS_ONLY``: адрес берётся из ``dfs.namenode.https-address``."""
    _write_xml(
        conf_dir,
        "hdfs-site.xml",
        {"dfs.http.policy": "HTTPS_ONLY", "dfs.namenode.https-address": "namenode:9871"},
    )

    assert hadoop_conf.resolve_webhdfs_urls() == ["https://namenode:9871"]


def test_resolve_returns_empty_when_nothing_known(conf_dir: pathlib.Path) -> None:
    """Ни адреса, ни ``fs.defaultFS`` — пустой список, а не исключение."""
    _write_xml(conf_dir, "hdfs-site.xml", {"dfs.replication": "1"})
    _write_xml(conf_dir, "core-site.xml", {"hadoop.tmp.dir": "/tmp"})

    assert hadoop_conf.resolve_webhdfs_urls() == []
