"""Cluster policy стенда: точечная инъекция OpenLineage в Spark-джобы Airflow.

OL-листенер вынесен из общего spark-defaults.conf (он ломал интерактивный
spark-shell). Airflow добавляет OL только своим SparkSubmitOperator-таскам через
cluster policy — так лайнидж пишется без правок в самих DAG'ах.

OL включается ТОЛЬКО если openlineage-spark jar реально лежит в HDFS
(``OPENLINEAGE_JAR``): без jar на драйвере ``spark.extraListeners`` упал бы с
``ClassNotFoundException`` и уронил джобу. Если jar не задан или отсутствует в
HDFS — OL молча не включается, пишется предупреждение, а джоба идёт без лайниджа.
"""

from __future__ import annotations

import logging
import os
import time
from typing import TYPE_CHECKING
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import urlopen

if TYPE_CHECKING:
    from airflow.models import BaseOperator

_log = logging.getLogger(__name__)

# HTTP-порт NameNode (WebHDFS) — отдельный от RPC-порта в hdfs://host:9000/... URI.
_WEBHDFS_PORT = 9870
# Кэш «jar есть в HDFS?» с TTL: парсинг DAG'ов частый, дёргать HDFS на каждую таску
# дорого. TTL заодно даёт подхватить jar, залитый уже после старта планировщика.
_JAR_CHECK_TTL_SEC = 60.0
_jar_cache: dict[str, tuple[bool, float]] = {}

# Путь к jar статичен на процесс; незаданность — разовое предупреждение при импорте.
_OPENLINEAGE_JAR = os.environ.get("OPENLINEAGE_JAR") or ""
if not _OPENLINEAGE_JAR:
    _log.warning("OpenLineage отключён: OPENLINEAGE_JAR не задан — лайнидж Airflow писаться не будет")


def _jar_in_hdfs(jar_uri: str) -> bool:
    """Проверяет наличие jar в HDFS через WebHDFS GETFILESTATUS (кэш с TTL).

    :param jar_uri: URI вида ``hdfs://host:port/path`` к openlineage-spark jar.
    :return: True если jar доступен; False если его нет или проверка не удалась —
        недоступность трактуем как «нет», безопаснее не включать OL, чем уронить джобу.
    """
    now = time.monotonic()
    cached = _jar_cache.get(jar_uri)
    if cached is not None and now - cached[1] < _JAR_CHECK_TTL_SEC:
        return cached[0]

    parsed = urlparse(jar_uri)
    host = parsed.hostname or "namenode"
    url = f"http://{host}:{_WEBHDFS_PORT}/webhdfs/v1{parsed.path}?op=GETFILESTATUS"
    available = False
    try:
        with urlopen(url, timeout=4) as resp:  # noqa: S310 — URL строится нами, всегда http://
            available = resp.status == 200
    except HTTPError as exc:
        if exc.code != 404:
            _log.warning("OpenLineage: WebHDFS вернул %s для %s", exc.code, jar_uri)
    except URLError as exc:
        _log.warning("OpenLineage: не удалось проверить jar в HDFS (%s): %s", jar_uri, exc.reason)

    _jar_cache[jar_uri] = (available, now)
    if not available:
        _log.warning(
            "OpenLineage не включён: jar отсутствует/недоступен в HDFS (%s). "
            "Залейте его: scripts/seed-openlineage-jar.bat",
            jar_uri,
        )
    return available


def task_policy(task: BaseOperator) -> None:
    """Домешивает OpenLineage-конфиг в conf каждого SparkSubmitOperator.

    OL включается только если jar доступен в HDFS (см. докстринг модуля); иначе
    таска остаётся без изменений и идёт без лайниджа.

    :param task: любой оператор Airflow; мутируется на месте на этапе парсинга DAG.
    :return: None.
    """
    from airflow.providers.apache.spark.operators.spark_submit import SparkSubmitOperator

    if not isinstance(task, SparkSubmitOperator):
        return

    # Без jar в HDFS listener упал бы ClassNotFoundException — OL не включаем вовсе.
    if not _OPENLINEAGE_JAR or not _jar_in_hdfs(_OPENLINEAGE_JAR):
        return

    ol = {
        "spark.extraListeners": "io.openlineage.spark.agent.OpenLineageSparkListener",
        "spark.openlineage.transport.type": "http",
        # `or`, а не default второго аргумента: compose подставляет пустую строку,
        # если переменной нет в .env (OPENLINEAGE_URL: ${OPENLINEAGE_URL}).
        "spark.openlineage.transport.url": os.environ.get("OPENLINEAGE_URL") or "http://marquez:5000",
        "spark.openlineage.namespace": os.environ.get("OPENLINEAGE_NAMESPACE") or "hadoop-cluster",
        "spark.openlineage.columnLineage.datasetLineageEnabled": "true",
    }
    # conf, заданный в DAG, побеждает: не затираем осознанные переопределения.
    # Провайдер apache-airflow-providers-apache-spark 4.1.1 хранит conf в приватном
    # _conf (публичного conf нет); execute() строит hook именно из self._conf.
    task._conf = {**ol, **(task._conf or {})}

    # jar доступен в HDFS — дотаскиваем его на драйвер через spark.jars
    # (deploy-mode=cluster → YARN локализует). Добавляем к DAG-jar'ам, не затирая.
    existing = [j for j in (task._conf.get("spark.jars") or "").split(",") if j]
    if _OPENLINEAGE_JAR not in existing:
        existing.append(_OPENLINEAGE_JAR)
    task._conf["spark.jars"] = ",".join(existing)
