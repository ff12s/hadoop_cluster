"""Cluster policy стенда: инъекция OpenLineage в Spark-таски Airflow.

OL-листенер вынесен из общего ``spark-defaults.conf`` (он ломал интерактивный
``spark-shell``), поэтому Airflow навешивает лайнидж своим ``SparkSubmitOperator``
сам — без правок в DAG'ах.

Что где решается. На **парсе** DAG-файла решается только то, для чего не нужен
метастор: подходит ли таска, не выключил ли лайнидж сам DAG (``params``) и лежит
ли openlineage-spark jar в HDFS. Адрес и namespace живут в Airflow Variable
``openlineage_config`` и читаются на **рендере** шаблонов — через макрос
``__openlineage_v1``, который политика кладёт в ``dag.user_defined_macros``.
Так парс не ходит в БД, а правка Variable в UI действует со следующего запуска
таски.

Политика ничего не роняет: любая ошибка гасится и превращается в «лайниджа нет».
Модуль намеренно не импортирует Airflow на уровне модуля — импорт идёт внутри
функций, поэтому набор тестов запускается без установленного Airflow.
"""

from __future__ import annotations

import functools
import importlib
import json
import os
import threading
from types import SimpleNamespace
from typing import Tuple, Type
from urllib.error import HTTPError
from urllib.parse import quote, urlparse
from urllib.request import urlopen

from . import handlers
from . import logger
from . import utils
from . import hadoop_conf

MACRO = "__openlineage_v1"
LISTENER = "io.openlineage.spark.agent.OpenLineageSparkListener"
VARIABLE = "openlineage_config"

# Ограничители зонда (§6.1): дедлайн на весь перебор, таймаут одного эндпоинта,
# TTL мемо. Модульные, потому что тесты подменяют их monkeypatch'ем.
_PROBE_DEADLINE_SEC = 5.0
ENDPOINT_TIMEOUT_SEC = 2.0
_MEMO_TTL_SEC = 300.0

# Ре-экспорт time-источника для тестов: monkeypatch.setattr(ol_policy, "_now", ...)
# должен попасть в нужный символ, а не в ``utils.now``.
_now = utils.now

# Мемо зонда: jar_uri -> (available, timestamp). Время — по ``_now``.
_jar_memo: dict[str, tuple[bool, float]] = {}

# Кортеж собирается лениво, при первом вызове политики: airflow_local_settings
# импортируется из settings.initialize() до configure_orm(), и импорт подмодуля
# Airflow на импорте политики шёл бы из частично инициализированного пакета.
_PASSTHROUGH_NAMES = ("AirflowTaskTimeout", "AirflowClusterPolicyViolation", "AirflowClusterPolicySkipDag")
_passthrough_cache: tuple[type[BaseException], ...] | None = None

_ATTR_CANDIDATES: dict[str, tuple[str, ...]] = {"conf": ("conf", "_conf"), "jars": ("jars", "_jars")}



# ---------------------------------------------------------------------------
# Служебное: лог, время, состояние модуля
# ---------------------------------------------------------------------------

def passthrough_exceptions() -> Tuple[Type[BaseException], ...]:
    """Классы исключений, которые политика обязана пропускать наружу.

    Собирается поимённо, каждый класс своим ``try/except``: в 2.6.3 нет
    ``AirflowClusterPolicySkipDag``, и общий ``import`` провалился бы целиком,
    молча выключив проброс ``AirflowTaskTimeout``.

    :return: кортеж классов; пустой, если Airflow недоступен.
    """
    global _passthrough_cache
    if _passthrough_cache is None:
        collected: tuple[type[BaseException], ...] = ()
        for name in _PASSTHROUGH_NAMES:
            try:
                collected += (getattr(importlib.import_module("airflow.exceptions"), name),)
            except (ImportError, AttributeError):
                continue
        _passthrough_cache = collected

    return _passthrough_cache

# ---------------------------------------------------------------------------
# Совместимость двух версий провайдера
# ---------------------------------------------------------------------------


def operator_attrs(task: object) -> SimpleNamespace | None:
    """Имена атрибутов conf и jars у этого оператора.

    Имя обязано одновременно быть в ``template_fields`` (значит, будет
    отрендерено) и существовать на объекте (значит, его читает hook). В
    провайдере 4.1.1 атрибуты приватные, в 4.10.0 — публичные, поэтому имя
    резолвится, а не зашивается.

    :param task: таска Airflow.
    :return: пространство имён с полями ``conf`` и ``jars``, либо None,
        если раскладка незнакома.
    """
    fields = set(getattr(task, "template_fields", ()) or ())
    resolved: dict[str, str] = {}
    for role, candidates in _ATTR_CANDIDATES.items():
        for name in candidates:
            if name in fields and hasattr(task, name):
                resolved[role] = name
                break
        else:
            return None
    return SimpleNamespace(**resolved)


def _spark_submit_operator() -> type | None:
    """Класс ``SparkSubmitOperator`` установленного провайдера.

    :return: класс оператора либо None, если провайдера в среде нет.
    """
    try:
        from airflow.providers.apache.spark.operators.spark_submit import SparkSubmitOperator
    except Exception:
        return None
    return SparkSubmitOperator


def _looks_like_spark_submit(task: object, operator_cls: type) -> bool:
    """Похожа ли таска на ``SparkSubmitOperator``, не будучи его экземпляром.

    Так распознаются динамически размапленные таски: их conf и jars лежат в
    ``partial_kwargs``, адресация через ``operator_attrs`` на них не работает.

    :param task: таска Airflow.
    :param operator_cls: класс оператора установленного провайдера.
    :return: True, если ``operator_class`` или ``task_type`` указывают на оператор.
    """
    operator_class = getattr(task, "operator_class", None)
    if operator_class is operator_cls:
        return True
    name = operator_class if isinstance(operator_class, str) else getattr(operator_class, "__name__", "")
    return name == operator_cls.__name__ or getattr(task, "task_type", None) == operator_cls.__name__


# ---------------------------------------------------------------------------
# Тумблер из DAG'а
# ---------------------------------------------------------------------------


def _level_forced(owner: object, level: str, dag_id: str, task_id: str) -> bool | None:
    """Значение тумблера одного уровня лесенки ``params``.

    Ключа нет или он равен None — уровень не высказался, и это нормальное
    состояние: warning'а нет. Негодное значение пишет warning и трактуется как
    отсутствующее.

    :param owner: таска либо DAG, чьи ``params`` читаются.
    :param level: имя уровня для сообщения ("таски" либо "DAG'а").
    :param dag_id: идентификатор DAG'а для ключа дедупликации.
    :param task_id: идентификатор таски для ключа дедупликации.
    :return: True, False либо None, если уровень не высказался.
    """
    params = getattr(owner, "params", None)
    if params is None:
        return None
    try:
        if "openlineage" not in params:
            return None
        value = params["openlineage"]
    except Exception:
        logger.warn_once(("unreadable-toggle", dag_id, task_id), "OpenLineage: не удалось прочитать params['openlineage'] — уровень %s игнорируется (%s.%s)", level, dag_id, task_id)
        return None
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    logger.warn_once(("bad-toggle", dag_id, task_id), "OpenLineage: params['openlineage'] не является булевым — уровень %s игнорируется (%s.%s)", level, dag_id, task_id)
    return None


def lineage_forced(task: object) -> bool | None:
    """Форс лайниджа из DAG'а: ``task.params``, затем ``dag.params``.

    :param task: таска Airflow.
    :return: True — форс-включение, False — форс-выключение, None — решение
        остаётся за Variable.
    """
    dag_id, task_id = utils.dag_and_task_ids(task)
    forced = _level_forced(task, "таски", dag_id, task_id)
    if forced is not None:
        return forced
    return _level_forced(utils.task_dag(task), "DAG'а", dag_id, task_id)


# ---------------------------------------------------------------------------
# Конфиг из Airflow Variable и макрос рендера
# ---------------------------------------------------------------------------


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

    :return: разобранный объект конфига (в том числе пустой) либо None, если
        конфиг прочитать не удалось; причина в этом случае уже записана в лог.
    """
    try:
        from airflow.models import Variable

        raw = Variable.get(VARIABLE, default_var=None)
    except Exception:
        logger.warn_once(("var-unavailable",), "OpenLineage выключен: Variable openlineage_config недоступна")
        return None
    if not raw:
        logger.warn_once(("no-var",), "OpenLineage выключен: Variable openlineage_config не задана")
        return None
    try:
        parsed = json.loads(raw)
    except Exception:
        logger.warn_once(("bad-json",), "OpenLineage выключен: Variable openlineage_config — не разбираемый JSON")
        return None
    if not isinstance(parsed, dict):
        logger.warn_once(("not-object",), "OpenLineage выключен: Variable openlineage_config — не JSON-объект")
        return None
    if "auth" in parsed:
        logger.warn_once(("auth",), "OpenLineage: ключ 'auth' в Variable не поддерживается и не подставляется")
    return parsed


def ol_macro(field: str, forced: bool | None = None) -> str:
    """Единственная точка решения «включён ли лайнидж» и единственный источник значений.

    Зовётся из Jinja на рендере таски. Не бросает никогда: битый конфиг обязан
    давать «лайниджа нет», а не падение рендера.

    :param field: "listener", "url" либо "namespace".
    :param forced: True, если DAG форсировал включение; None — форса нет.
    :return: значение поля либо "", если лайнидж выключен или конфиг негоден.
    """
    cfg = _cfg()
    if cfg is None:
        return ""
    enabled = cfg.get("enabled")
    if not (forced is True or enabled is True):
        # Молча — только когда выключение осознанное: enabled ровно False.
        if enabled is not False:
            logger.warn_once(("bad-enabled",), "OpenLineage выключен: в Variable openlineage_config поле enabled отсутствует или не является булевым")
        return ""
    url = _clean(cfg.get("url"), require_scheme=True)
    namespace = _clean(cfg.get("namespace"))
    if not url or not namespace:
        logger.warn_once(("bad-field",), "OpenLineage не включён: в Variable openlineage_config негодно поле %s", "url" if not url else "namespace")
        return ""
    return {"listener": LISTENER, "url": url, "namespace": namespace}.get(field, "")


def ol_conf_template(forced_on: bool) -> dict[str, str]:
    """Значения conf: три из пяти — вызовы одного макроса, решение целиком в Python.

    :param forced_on: True, если DAG форсировал включение. Форс-выключение до
        этой функции не доходит — его отсекает гейт в ``inject_openlineage``.
    :return: словарь conf-ключей, где три значения — Jinja-вызовы макроса.
    """
    forced = "true" if forced_on else "none"
    return {
        "spark.extraListeners": f"{{{{ {MACRO}('listener', {forced}) }}}}",
        "spark.openlineage.transport.type": "http",
        "spark.openlineage.transport.url": f"{{{{ {MACRO}('url', {forced}) }}}}",
        "spark.openlineage.namespace": f"{{{{ {MACRO}('namespace', {forced}) }}}}",
        "spark.openlineage.columnLineage.datasetLineageEnabled": "true",
    }


_OUR_LISTENERS = frozenset(ol_conf_template(forced)["spark.extraListeners"] for forced in (True, False))


def foreign_listener(cur_conf: dict[str, object]) -> bool:
    """Задан ли в conf таски не наш ``spark.extraListeners``.

    Чужим считается значение, а не наличие ключа: иначе повторный прогон
    политики по той же таске принял бы собственный шаблон за чужой листенер.

    :param cur_conf: conf таски, каким он был до политики.
    :return: True, если ключ задан непустым значением и это не шаблон политики.
    """
    value = cur_conf.get("spark.extraListeners")
    return bool(value) and value not in _OUR_LISTENERS


# ---------------------------------------------------------------------------
# Зонд jar в HDFS
# ---------------------------------------------------------------------------


def jar_path(jar_uri: str) -> str | None:
    """Путь внутри HDFS из значения ``OPENLINEAGE_JAR``.

    Схема обязательна: значение без схемы ``spark-submit`` трактует в ``--jars``
    как локальный файл сабмит-хоста, и джоба падает на локализации. Authority
    (RPC-хост и RPC-порт) игнорируется — эндпоинты WebHDFS даёт резолвер.

    :param jar_uri: значение переменной окружения ``OPENLINEAGE_JAR``.
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


def _query_endpoint(endpoint: str, path: str) -> str:
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
        logger.warn_once(("all-standby",), "OpenLineage не включён: все NameNode ответили standby (%s)", path)
    else:
        logger.warn_once(("endpoints-down",), "OpenLineage не включён: эндпоинты WebHDFS недоступны (%s)", path)
    return False


# Ре-экспорт резолвера эндпоинтов, чтобы тесты могли подменять его через
# ``monkeypatch.setattr(ol_policy, "resolve_webhdfs_urls", ...)``.
resolve_webhdfs_urls = hadoop_conf.resolve_webhdfs_urls


def _probe_worker(path: str, slot: list[tuple[str, object]]) -> None:
    """Тело демон-потока зонда: кладёт в слот результат либо исключение.

    :param path: абсолютный путь jar'а в HDFS.
    :param slot: список-слот, куда кладётся ровно один кортеж.
    :return: None.
    """
    try:
        slot.append(("ok", _probe(path)))
    except Exception as error:
        slot.append(("err", error))


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

    :param jar_uri: исходное значение ``OPENLINEAGE_JAR`` — оно же ключ мемо.
    :param path: разобранный путь jar'а для WebHDFS.
    :return: True, если jar доступен; False во всех остальных исходах.
    """
    cached = _jar_memo.get(jar_uri)
    if cached is not None:
        value, stamped = cached
        if _now() - stamped < _MEMO_TTL_SEC:
            return value
        _jar_memo.pop(jar_uri, None)

    slot: list[tuple[str, object]] = []
    worker = threading.Thread(target=_probe_worker, args=(path, slot), daemon=True, name="openlineage-jar-probe")
    worker.start()
    worker.join(_PROBE_DEADLINE_SEC)

    if not slot:
        available = False
        logger.warn_once(("probe-deadline",), "OpenLineage не включён: зонд jar не уложился в дедлайн %s с (%s)", _PROBE_DEADLINE_SEC, jar_uri)
    else:
        kind, payload = slot[0]
        if kind == "ok":
            available = bool(payload)
        else:
            available = False
            if isinstance(payload, handlers.NoEndpointsError):
                logger.warn_once(("no-endpoints",), "OpenLineage не включён: эндпоинты WebHDFS не определены по HADOOP_CONF_DIR (%s)", payload)
            else:
                logger.warn_once(("probe-error",), "OpenLineage не включён: не удалось определить эндпоинты WebHDFS (%s): %s", jar_uri, payload)

    _jar_memo[jar_uri] = (available, _now())
    return available


def inject_openlineage(task: object) -> None:
    """Навешивает OpenLineage на уже проверенную Spark-таску.

    Порядок гейтов нормативен: форс-выключение проверяется раньше conf, поэтому
    DAG, который и выключил тумблер, и сам задал листенер, уходит тихо. Порядок
    трёх мутаций тоже: обрыв оставляет таску максимум с лишним jar'ом на
    classpath, но без листенера — то есть без лайниджа, что безопасно.

    :param task: экземпляр ``SparkSubmitOperator``; мутируется на месте.
    :return: None.
    """
    dag_id, task_id = utils.dag_and_task_ids(task)

    attrs = operator_attrs(task)
    if attrs is None:
        logger.warn_once(("unknown-layout", dag_id, task_id), "OpenLineage не включён: незнакомая раскладка атрибутов оператора (%s.%s)", dag_id, task_id)
        return

    forced = lineage_forced(task)
    if forced is False:
        return

    cur_conf = dict(getattr(task, attrs.conf) or {})


    dag = utils.task_dag(task)
    if dag is None:
        logger.warn_once(("no-dag", dag_id, task_id), "OpenLineage не включён: таска не привязана к DAG, макрос положить некуда (%s)", task_id)
        return

    # Макрос кладётся в DAG политикой намеренно: это единственный способ отложить
    # чтение Variable до рендера таски, ничего не требуя от автора DAG'а.
    # Копия: DAG не трогаем до конца вычислений. Чужим считается только объект,
    # который не является нашей функцией, — иначе вторая таска файла увидела бы
    # чужим то, что положила первая.
    macros = dict(getattr(dag, "user_defined_macros", None) or {})
    if MACRO in macros and macros[MACRO] is not ol_macro:
        logger.warn_once(("macro-taken", dag_id, task_id), "OpenLineage не включён: имя макроса %s занято чужим объектом (%s.%s)", MACRO, dag_id, task_id)
        return

    jar_uri = os.environ.get("OPENLINEAGE_JAR", "")
    path = jar_path(jar_uri)
    if path is None:
        if not jar_uri.strip():
            logger.warn_once(("jar-unset",), "OpenLineage не включён: OPENLINEAGE_JAR не задан")
        else:
            logger.warn_once(("jar-malformed",), "OpenLineage не включён: OPENLINEAGE_JAR задан без схемы или без пути (%s)", jar_uri)
        return
    if not jar_available(jar_uri, path):
        logger.warn_once(("jar-absent", dag_id, task_id),"OpenLineage не включён: jar отсутствует или недоступен в HDFS (%s), таска %s.%s. Залейте его: scripts/seed-openlineage-jar.bat", jar_uri, dag_id, task_id)
        return

    merged_conf = {**ol_conf_template(forced is True), **cur_conf}
    merged_jars = utils.merge_jars(getattr(task, attrs.jars), cur_conf.get("spark.jars"), jar_uri)

    macros[MACRO] = ol_macro
    dag.user_defined_macros = macros
    setattr(task, attrs.jars, merged_jars)
    setattr(task, attrs.conf, merged_conf)


def apply_policy(task: object) -> None:
    """Точка входа cluster policy: гейт типа таски и общий перехват ошибок.

    Любая ошибка политики гасится: исключение отсюда роняет импорт всего
    DAG-файла, то есть баг выключил бы все DAG'и разом. Чужой механизм таймаута
    и чужое решение пропустить DAG пробрасываются наружу.

    :param task: любая таска Airflow; мутируется на месте на этапе парсинга.
    :return: None.
    """
    try:
        operator_cls = _spark_submit_operator()
        if operator_cls is None:
            return
        if not isinstance(task, operator_cls):
            if _looks_like_spark_submit(task, operator_cls):
                dag_id, task_id = utils.dag_and_task_ids(task)
                logger.warn_once(("mapped", dag_id, task_id), "OpenLineage не включён: динамический маппинг тасок не поддерживается (%s.%s)", dag_id, task_id)
            return
        inject_openlineage(task)
    except passthrough_exceptions():
        raise
    except Exception:
        dag_id, task_id = utils.dag_and_task_ids(task)
        logger.warn_once(("unexpected", dag_id, task_id), "OpenLineage не включён: непредвиденная ошибка cluster policy (%s.%s)", dag_id, task_id, exc_info=True)


def reset_state() -> None:
    """Сбрасывает всё модульное состояние политики.

    Зовётся фикстурой ``_reset_policy_state`` (conftest.py) до и после каждого
    теста: дедупликация warning'ов, кэш ``_cfg``, мемо зонда и кэш классов
    исключений переживают границу теста и без сброса смешали бы результаты.

    :return: None.
    """
    global _passthrough_cache
    logger._warned.clear()
    _cfg.cache_clear()
    _jar_memo.clear()
    _passthrough_cache = None


# Реэкспорт утилит: тесты и вызывающий код обращаются к ним через пакет политики.
merge_jars = utils.merge_jars
