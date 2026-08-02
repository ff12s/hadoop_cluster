"""Тесты cluster policy OpenLineage (§9 спеки).

Набор запускается голым ``python -m pytest`` без установленного Airflow: политика
не импортирует его на уровне модуля, а провайдерские классы подменяются дублями.
Каждый тест раскладки атрибутов прогоняется дважды — на приватной раскладке
провайдера 4.1.1 и на публичной раскладке 4.10.0.
"""

from __future__ import annotations

import base64
import importlib
import io
import json
import logging
import sys
import threading
import time
import types
from types import SimpleNamespace
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request

import pytest

import ol_policy
from conftest import DummyDag, warnings_of
from ol_policy import callback

JAR = "hdfs://namenode:9000/opt/openlineage/openlineage-spark_2.13-1.46.0.jar"
FOREIGN_LISTENER = "com.example.OtherListener"


# ---------------------------------------------------------------------------
# Вспомогательные дубли и фикстуры
# ---------------------------------------------------------------------------


def make_task(
    layout: SimpleNamespace,
    *,
    dag: object = None,
    conf: dict[str, object] | None = None,
    jars: str | None = None,
    params: object = None,
    task_id: str = "submit",
) -> object:
    """Создаёт дубль таски нужной раскладки, по умолчанию привязанный к DAG'у.

    :param layout: раскладка атрибутов.
    :param dag: DAG таски; None означает «создать свой ``DummyDag``».
    :param conf: начальный conf.
    :param jars: начальное значение атрибута jars.
    :param params: params таски.
    :param task_id: идентификатор таски.
    :return: дубль таски.
    """
    return layout.cls(task_id=task_id, dag=DummyDag() if dag is None else dag, conf=conf, jars=jars, params=params)


def conf_of(task: object, layout: SimpleNamespace) -> dict[str, object] | None:
    """conf таски, прочитанный через имя атрибута текущей раскладки.

    :param task: дубль таски.
    :param layout: раскладка атрибутов.
    :return: значение атрибута conf.
    """
    return getattr(task, layout.conf)


def jars_of(task: object, layout: SimpleNamespace) -> str | None:
    """Значение атрибута jars текущей раскладки.

    :param task: дубль таски.
    :param layout: раскладка атрибутов.
    :return: значение атрибута jars.
    """
    return getattr(task, layout.jars)


class FakeResponse:
    """Дубль ответа ``urlopen``: только статус и протокол контекстного менеджера."""

    def __init__(self, status: int) -> None:
        """Создаёт дубль ответа.

        :param status: HTTP-код ответа.
        :return: None.
        """
        self.status = status

    def __enter__(self) -> "FakeResponse":
        """Вход в контекстный менеджер.

        :return: сам объект.
        """
        return self

    def __exit__(self, *exc_info: object) -> bool:
        """Выход из контекстного менеджера.

        :param exc_info: сведения об исключении.
        :return: False — исключения не гасятся.
        """
        return False


def standby_error(url: str) -> HTTPError:
    """Ответ standby-NameNode: 403 с ``RemoteException.exception == StandbyException``.

    :param url: адрес запроса.
    :return: исключение ``HTTPError`` с телом standby.
    """
    body = json.dumps({"RemoteException": {"exception": "StandbyException"}}).encode("utf-8")
    return HTTPError(url, 403, "Forbidden", {}, io.BytesIO(body))


def _http_error(code: int, body: bytes = b"") -> HTTPError:
    """Строит HTTPError с телом для подмены urlopen.

    :param code: HTTP-код ответа.
    :param body: тело ответа.
    :return: экземпляр HTTPError.
    """
    return HTTPError("url", code, "msg", None, io.BytesIO(body))


def _fake_spnego(monkeypatch: pytest.MonkeyPatch, token: bytes = b"tok") -> None:
    """Подставляет дубль модуля spnego, отдающий заданный токен.

    :param monkeypatch: фикстура подмены.
    :param token: байты токена, которые вернёт step().
    :return: None.
    """
    ctx = SimpleNamespace(step=lambda in_token=None: token)
    module = types.ModuleType("spnego")
    module.client = lambda hostname, service, protocol: ctx
    monkeypatch.setitem(sys.modules, "spnego", module)


@pytest.fixture
def jar_env(monkeypatch: pytest.MonkeyPatch) -> str:
    """Задаёт ``OPENLINEAGE_JAR`` штатным значением стенда.

    :param monkeypatch: фикстура подмены.
    :return: значение переменной.
    """
    monkeypatch.setenv("OPENLINEAGE_JAR", JAR)
    return JAR


@pytest.fixture
def jar_ok(monkeypatch: pytest.MonkeyPatch, jar_env: str) -> list[tuple[str, str]]:
    """Подменяет зонд успешным ответом и записывает его вызовы.

    :param monkeypatch: фикстура подмены.
    :param jar_env: заданная переменная ``OPENLINEAGE_JAR``.
    :return: список аргументов вызовов зонда.
    """
    calls: list[tuple[str, str]] = []

    def _available(jar_uri: str, path: str) -> bool:
        calls.append((jar_uri, path))
        return True

    monkeypatch.setattr(ol_policy.probe, "jar_available", _available)
    return calls


@pytest.fixture
def probe_forbidden(monkeypatch: pytest.MonkeyPatch) -> None:
    """Валит тест, если зонд вообще был вызван.

    :param monkeypatch: фикстура подмены.
    :return: None.
    """

    def _fail(jar_uri: str, path: str) -> bool:
        raise AssertionError("зонд не должен вызываться")

    monkeypatch.setattr(ol_policy.probe, "jar_available", _fail)


@pytest.fixture
def endpoints(monkeypatch: pytest.MonkeyPatch) -> Callable[[list[str]], None]:
    """Подменяет резолвер эндпоинтов WebHDFS заданным списком.

    :param monkeypatch: фикстура подмены.
    :return: функция-настройщик списка эндпоинтов.
    """

    def _set(urls: list[str]) -> None:
        """Подменяет резолвер эндпоинтов заданным списком URL.

        :param urls: список URL эндпоинтов WebHDFS.
        :return: None.
        """
        monkeypatch.setattr(ol_policy.probe, "resolve_webhdfs_urls", lambda: list(urls))

    return _set


@pytest.fixture
def requests_log(monkeypatch: pytest.MonkeyPatch) -> Callable[[Callable[[object], object]], list[object]]:
    """Подменяет ``urlopen`` заданным обработчиком и пишет запрошенные URL.

    :param monkeypatch: фикстура подмены.
    :return: функция-настройщик, возвращающая список запрошенных URL.
    """
    urls: list[object] = []

    def _install(handler: Callable[[object], object]) -> list[object]:
        """Подставляет ``urlopen`` дублём, делегирующим ответ заданному обработчику.

        :param handler: обработчик, отдающий ответ или исключение по запрошенному URL.
        :return: список запрошенных URL, пополняемый при каждом вызове дубля.
        """

        def _urlopen(url: object, timeout: float | None = None) -> object:
            """Дубль ``urlopen``: журналирует URL и делегирует ответ обработчику.

            :param url: запрошенный URL либо объект ``Request``.
            :param timeout: таймаут запроса; дублем не используется.
            :return: результат обработчика.
            :raises BaseException: если обработчик вернул исключение вместо ответа.
            """
            urls.append(url)
            result = handler(url)
            if isinstance(result, BaseException):
                raise result
            return result

        monkeypatch.setattr(ol_policy.probe, "urlopen", _urlopen)
        return urls

    return _install


def install_airflow_exceptions(monkeypatch: pytest.MonkeyPatch, names: tuple[str, ...]) -> SimpleNamespace:
    """Подставляет модуль ``airflow.exceptions`` с перечисленными классами.

    :param monkeypatch: фикстура подмены.
    :param names: имена классов исключений, которые модуль обязан содержать.
    :return: пространство имён с созданными классами.
    """
    module = types.ModuleType("airflow.exceptions")
    created: dict[str, type[BaseException]] = {}
    for name in names:
        created[name] = type(name, (Exception,), {})
        setattr(module, name, created[name])
    package = types.ModuleType("airflow")
    package.exceptions = module
    monkeypatch.setitem(sys.modules, "airflow", package)
    monkeypatch.setitem(sys.modules, "airflow.exceptions", module)
    return SimpleNamespace(**created)


# ---------------------------------------------------------------------------
# Раскладка атрибутов оператора (инвариант 8)
# ---------------------------------------------------------------------------


def test_operator_attrs_resolves_layout(layout: SimpleNamespace) -> None:
    """Имена conf/jars берутся по факту, а не зашиты.

    :param layout: раскладка атрибутов conf/jars текущего провайдера (параметризована).
    :return: None.
    """
    attrs = ol_policy.operator_attrs(make_task(layout))

    assert (attrs.conf, attrs.jars) == (layout.conf, layout.jars)


class FieldWithoutAttribute:
    """Раскладка, где имя есть в ``template_fields``, а атрибута нет."""

    template_fields = ("conf", "jars")

    def __init__(self) -> None:
        """Создаёт дубль без атрибутов conf/jars.

        :return: None.
        """
        self.task_id = "submit"
        self.dag = DummyDag()
        self.params = {}


class AttributeWithoutField:
    """Раскладка, где атрибут есть, а в ``template_fields`` его нет."""

    template_fields = ("application",)

    def __init__(self) -> None:
        """Создаёт дубль с conf/jars вне ``template_fields``.

        :return: None.
        """
        self.task_id = "submit"
        self.dag = DummyDag()
        self.params = {}
        self.conf = {}
        self.jars = None


class NeitherFieldNorAttribute:
    """Раскладка без conf/jars вовсе."""

    template_fields = ()

    def __init__(self) -> None:
        """Создаёт дубль без conf/jars и без соответствующих полей шаблона.

        :return: None.
        """
        self.task_id = "submit"
        self.dag = DummyDag()
        self.params = {}


@pytest.mark.parametrize("broken", [FieldWithoutAttribute, AttributeWithoutField, NeitherFieldNorAttribute])
def test_unknown_layout_warns_and_creates_nothing(
    broken: type, jar_ok: list[tuple[str, str]], caplog: pytest.LogCaptureFixture
) -> None:
    """Незнакомая раскладка: warning и ни одного созданного атрибута.

    :param broken: класс негодной раскладки (параметризован).
    :param jar_ok: фикстура, подменяющая зонд успешным ответом; список вызовов зонда.
    :param caplog: фикстура pytest для перехвата записей лога.
    :return: None.
    """
    task = broken()
    before = dict(vars(task))

    ol_policy.inject_openlineage(task)

    assert vars(task) == before
    assert any("раскладка" in message for message in warnings_of(caplog))


# ---------------------------------------------------------------------------
# apply_policy дописывает колбэк на парсе: гейты + идемпотентность
# ---------------------------------------------------------------------------


def test_policy_appends_callback(layout: SimpleNamespace, spark_operator: type, probe_forbidden: None) -> None:
    """apply_policy дописывает колбэк, не читая ни Variable, ни сеть.

    :param layout: раскладка атрибутов conf/jars текущего провайдера (параметризована).
    :param spark_operator: фикстура, подменяющая распознавание ``SparkSubmitOperator`` дублём текущей раскладки.
    :param probe_forbidden: фикстура, которая валит тест при любом обращении к зонду.
    :return: None.
    """
    task = layout.cls(dag=DummyDag())
    ol_policy.apply_policy(task)
    assert task.on_execute_callback == [ol_policy.callback.ol_execute_callback]


def test_policy_append_is_idempotent(layout: SimpleNamespace, spark_operator: type) -> None:
    """Повторный apply_policy не дублирует колбэк.

    :param layout: раскладка атрибутов conf/jars текущего провайдера (параметризована).
    :param spark_operator: фикстура, подменяющая распознавание ``SparkSubmitOperator`` дублём текущей раскладки.
    :return: None.
    """
    task = layout.cls(dag=DummyDag())
    ol_policy.apply_policy(task)
    ol_policy.apply_policy(task)
    assert task.on_execute_callback.count(ol_policy.callback.ol_execute_callback) == 1


def test_policy_keeps_author_callback_first(layout: SimpleNamespace, spark_operator: type) -> None:
    """Авторский колбэк (одиночный и списочный) сохранён и стоит раньше нашего.

    :param layout: раскладка атрибутов conf/jars текущего провайдера (параметризована).
    :param spark_operator: фикстура, подменяющая распознавание ``SparkSubmitOperator`` дублём текущей раскладки.
    :return: None.
    """
    author = lambda context: None  # noqa: E731
    task = layout.cls(dag=DummyDag())
    task.on_execute_callback = author
    ol_policy.apply_policy(task)
    assert task.on_execute_callback == [author, ol_policy.callback.ol_execute_callback]


def test_policy_force_off_appends_nothing(layout: SimpleNamespace, spark_operator: type) -> None:
    """Форс-выключение на парсе: колбэк не навешивается, таска нетронута.

    :param layout: раскладка атрибутов conf/jars текущего провайдера (параметризована).
    :param spark_operator: фикстура, подменяющая распознавание ``SparkSubmitOperator`` дублём текущей раскладки.
    :return: None.
    """
    task = layout.cls(dag=DummyDag(), params={"openlineage": False})
    ol_policy.apply_policy(task)
    assert task.on_execute_callback is None


def test_policy_does_not_touch_dag_and_conf(layout: SimpleNamespace, spark_operator: type) -> None:
    """Парс не трогает ни conf, ни jars, ни user_defined_macros DAG'а.

    :param layout: раскладка атрибутов conf/jars текущего провайдера (параметризована).
    :param spark_operator: фикстура, подменяющая распознавание ``SparkSubmitOperator`` дублём текущей раскладки.
    :return: None.
    """
    dag = DummyDag()
    task = layout.cls(dag=dag, conf={"k": "v"}, jars="a.jar")
    ol_policy.apply_policy(task)
    assert getattr(task, layout.conf) == {"k": "v"}
    assert getattr(task, layout.jars) == "a.jar"
    assert dag.user_defined_macros is None


def test_full_cycle_parse_then_callback(
    layout: SimpleNamespace,
    spark_operator: type,
    variable: Callable[..., SimpleNamespace],
    jar_ok: list[tuple[str, str]],
) -> None:
    """apply_policy + вызов колбэков списком даёт готовые значения spark-submit.

    :param layout: раскладка атрибутов conf/jars текущего провайдера (параметризована).
    :param spark_operator: фикстура, подменяющая распознавание ``SparkSubmitOperator`` дублём текущей раскладки.
    :param variable: фикстура, подменяющая чтение Variable ``openlineage_config``.
    :param jar_ok: фикстура, подменяющая зонд успешным ответом; список вызовов зонда.
    :return: None.
    """
    variable(raw=VALID_VARIABLE)
    task = layout.cls(dag=DummyDag(), conf={"spark.executor.cores": "2"}, jars="hdfs:///user/app.jar")
    ol_policy.apply_policy(task)
    callbacks = task.on_execute_callback
    for cb in callbacks if isinstance(callbacks, list) else [callbacks]:
        cb({"task": task})
    conf = getattr(task, layout.conf)
    assert conf["spark.extraListeners"] == "io.openlineage.spark.agent.OpenLineageSparkListener"
    assert getattr(task, layout.jars) == "hdfs:///user/app.jar,hdfs:///jars/openlineage-spark.jar"


# ---------------------------------------------------------------------------
# Тумблер из DAG'а: таблица истинности §5.3, парсовая половина
# ---------------------------------------------------------------------------


def test_task_force_off_is_silent(
    layout: SimpleNamespace, probe_forbidden: None, caplog: pytest.LogCaptureFixture
) -> None:
    """Форс-выключение таски: тихий отказ без единой записи в лог.

    :param layout: раскладка атрибутов conf/jars текущего провайдера (параметризована).
    :param probe_forbidden: фикстура, которая валит тест при любом обращении к зонду.
    :param caplog: фикстура pytest для перехвата записей лога.
    :return: None.
    """
    task = make_task(layout, params={"openlineage": False})

    ol_policy.inject_openlineage(task)

    assert conf_of(task, layout) is None
    assert task.on_execute_callback is None
    assert warnings_of(caplog) == []


def test_dag_force_off_is_silent(
    layout: SimpleNamespace, probe_forbidden: None, caplog: pytest.LogCaptureFixture
) -> None:
    """Форс-выключение на уровне DAG'а действует так же, как на уровне таски.

    :param layout: раскладка атрибутов conf/jars текущего провайдера (параметризована).
    :param probe_forbidden: фикстура, которая валит тест при любом обращении к зонду.
    :param caplog: фикстура pytest для перехвата записей лога.
    :return: None.
    """
    task = make_task(layout, dag=DummyDag(params={"openlineage": False}))

    ol_policy.inject_openlineage(task)

    assert conf_of(task, layout) is None
    assert task.on_execute_callback is None
    assert warnings_of(caplog) == []


def test_task_force_on_beats_dag_force_off(layout: SimpleNamespace, caplog: pytest.LogCaptureFixture) -> None:
    """Форс таски перекрывает форс DAG'а: колбэк дописан.

    :param layout: раскладка атрибутов conf/jars текущего провайдера (параметризована).
    :param caplog: фикстура pytest для перехвата записей лога.
    :return: None.
    """
    task = make_task(
        layout,
        dag=DummyDag(params={"openlineage": False}),
        params={"openlineage": True},
    )

    ol_policy.inject_openlineage(task)

    assert task.on_execute_callback == [ol_policy.callback.ol_execute_callback]
    assert warnings_of(caplog) == []


def test_dag_force_on_is_used_when_task_is_silent(layout: SimpleNamespace, caplog: pytest.LogCaptureFixture) -> None:
    """Форс-включение DAG'а действует, если таска не высказалась.

    :param layout: раскладка атрибутов conf/jars текущего провайдера (параметризована).
    :param caplog: фикстура pytest для перехвата записей лога.
    :return: None.
    """
    task = make_task(layout, dag=DummyDag(params={"openlineage": True}))

    ol_policy.inject_openlineage(task)

    assert task.on_execute_callback == [ol_policy.callback.ol_execute_callback]
    assert warnings_of(caplog) == []


def test_missing_toggle_is_neutral_and_silent(layout: SimpleNamespace, caplog: pytest.LogCaptureFixture) -> None:
    """Ключа нет ни у таски, ни у DAG'а: решение уходит в колбэк, лог пуст.

    :param layout: раскладка атрибутов conf/jars текущего провайдера (параметризована).
    :param caplog: фикстура pytest для перехвата записей лога.
    :return: None.
    """
    task = make_task(layout)

    ol_policy.inject_openlineage(task)

    assert task.on_execute_callback == [ol_policy.callback.ol_execute_callback]
    assert warnings_of(caplog) == []


def test_none_toggle_is_neutral_and_silent(layout: SimpleNamespace, caplog: pytest.LogCaptureFixture) -> None:
    """Объявленный нейтральный ``None`` молчит так же, как отсутствие ключа.

    :param layout: раскладка атрибутов conf/jars текущего провайдера (параметризована).
    :param caplog: фикстура pytest для перехвата записей лога.
    :return: None.
    """
    task = make_task(layout, params={"openlineage": None}, dag=DummyDag(params={"openlineage": None}))

    ol_policy.inject_openlineage(task)

    assert task.on_execute_callback == [ol_policy.callback.ol_execute_callback]
    assert warnings_of(caplog) == []


@pytest.mark.parametrize("value", ["yes", 1, [], {}])
def test_non_bool_toggle_warns_and_falls_through(
    layout: SimpleNamespace, caplog: pytest.LogCaptureFixture, value: object
) -> None:
    """Негодное значение тумблера игнорируется с warning'ом, решение уходит ниже.

    :param layout: раскладка атрибутов conf/jars текущего провайдера (параметризована).
    :param caplog: фикстура pytest для перехвата записей лога.
    :param value: негодное значение тумблера (параметризовано).
    :return: None.
    """
    task = make_task(layout, params={"openlineage": value})

    ol_policy.inject_openlineage(task)

    assert task.on_execute_callback == [ol_policy.callback.ol_execute_callback]
    assert any("openlineage" in message for message in warnings_of(caplog))


def test_non_bool_task_toggle_does_not_hide_dag_force_off(
    layout: SimpleNamespace, probe_forbidden: None, caplog: pytest.LogCaptureFixture
) -> None:
    """Негодный уровень игнорируется целиком: решает следующий уровень лесенки.

    :param layout: раскладка атрибутов conf/jars текущего провайдера (параметризована).
    :param probe_forbidden: фикстура, которая валит тест при любом обращении к зонду.
    :param caplog: фикстура pytest для перехвата записей лога.
    :return: None.
    """
    task = make_task(layout, params={"openlineage": "yes"}, dag=DummyDag(params={"openlineage": False}))

    ol_policy.inject_openlineage(task)

    assert conf_of(task, layout) is None
    assert task.on_execute_callback is None


class RaisingParams(dict):
    """``params``, чьё чтение значения бросает — как ``ParamValidationError``."""

    def __getitem__(self, key: str) -> object:
        """Всегда бросает при чтении значения.

        :param key: имя параметра.
        :return: не возвращает.
        :raises ValueError: всегда.
        """
        raise ValueError("param validation failed")


def test_raising_params_warns_and_does_not_break(layout: SimpleNamespace, caplog: pytest.LogCaptureFixture) -> None:
    """Исключение при чтении ``params`` гасится: уровень игнорируется с warning'ом.

    :param layout: раскладка атрибутов conf/jars текущего провайдера (параметризована).
    :param caplog: фикстура pytest для перехвата записей лога.
    :return: None.
    """
    task = make_task(layout, params=RaisingParams({"openlineage": True}))

    ol_policy.inject_openlineage(task)

    assert task.on_execute_callback == [ol_policy.callback.ol_execute_callback]
    assert any("params" in message for message in warnings_of(caplog))


def test_lineage_forced_returns_only_tristate(layout: SimpleNamespace, caplog: pytest.LogCaptureFixture) -> None:
    """Наружу негодное значение тумблера не отдаётся никогда.

    :param layout: раскладка атрибутов conf/jars текущего провайдера (параметризована).
    :param caplog: фикстура pytest для перехвата записей лога.
    :return: None.
    """
    assert ol_policy.lineage_forced(make_task(layout, params={"openlineage": "yes"})) is None
    assert ol_policy.lineage_forced(make_task(layout, params={"openlineage": True})) is True
    assert ol_policy.lineage_forced(make_task(layout, params={"openlineage": False})) is False
    assert ol_policy.lineage_forced(make_task(layout)) is None


# ---------------------------------------------------------------------------
# Чужой spark.extraListeners (инвариант 9)
# ---------------------------------------------------------------------------


def test_force_off_beats_foreign_listener_gate(
    layout: SimpleNamespace, probe_forbidden: None, caplog: pytest.LogCaptureFixture
) -> None:
    """Форс-выключение сильнее гейта чужого листенера: лог пуст.

    :param layout: раскладка атрибутов conf/jars текущего провайдера (параметризована).
    :param probe_forbidden: фикстура, которая валит тест при любом обращении к зонду.
    :param caplog: фикстура pytest для перехвата записей лога.
    :return: None.
    """
    task = make_task(
        layout,
        conf={"spark.extraListeners": FOREIGN_LISTENER},
        params={"openlineage": False},
    )

    ol_policy.inject_openlineage(task)

    assert task.on_execute_callback is None

    assert conf_of(task, layout) == {"spark.extraListeners": FOREIGN_LISTENER}
    assert warnings_of(caplog) == []


# ---------------------------------------------------------------------------
# Дозапись колбэка: две таски одного DAG'а, таска без DAG'а
# ---------------------------------------------------------------------------


def test_two_tasks_of_one_dag_are_both_injected(layout: SimpleNamespace, caplog: pytest.LogCaptureFixture) -> None:
    """Обе таски одного DAG'а получают колбэк независимо друг от друга.

    :param layout: раскладка атрибутов conf/jars текущего провайдера (параметризована).
    :param caplog: фикстура pytest для перехвата записей лога.
    :return: None.
    """
    dag = DummyDag()
    first = make_task(layout, dag=dag, task_id="generate")
    second = make_task(layout, dag=dag, task_id="aggregate")

    ol_policy.inject_openlineage(first)
    ol_policy.inject_openlineage(second)

    assert first.on_execute_callback == [ol_policy.callback.ol_execute_callback]
    assert second.on_execute_callback == [ol_policy.callback.ol_execute_callback]
    assert warnings_of(caplog) == []


def test_task_without_dag_is_injected_quietly(layout: SimpleNamespace, caplog: pytest.LogCaptureFixture) -> None:
    """Гейт «таска без DAG» снят: колбэк класть некуда для макроса не нужно, таска дозаписана как обычно.

    :param layout: раскладка атрибутов conf/jars текущего провайдера (параметризована).
    :param caplog: фикстура pytest для перехвата записей лога.
    :return: None.
    """
    task = make_task(layout)
    task.dag = None

    ol_policy.inject_openlineage(task)

    assert task.on_execute_callback == [ol_policy.callback.ol_execute_callback]
    assert warnings_of(caplog) == []


# ---------------------------------------------------------------------------
# Разбор OPENLINEAGE_JAR (§5.2)
# ---------------------------------------------------------------------------


def test_jar_path_takes_path_only() -> None:
    """Путь берётся из URI, authority игнорируется.

    :return: None.
    """
    assert ol_policy.jar_path(JAR) == "/opt/openlineage/openlineage-spark_2.13-1.46.0.jar"


@pytest.mark.parametrize("value", ["", "   ", "/opt/openlineage/ol.jar", "hdfs://namenode:9000", "ol.jar"])
def test_jar_path_rejects_malformed(value: str) -> None:
    """Значение без схемы или без пути годным не считается.

    :param value: негодное значение поля ``openlineage_jar`` (параметризовано).
    :return: None.
    """
    assert ol_policy.jar_path(value) is None


def test_endpoint_host_comes_from_resolver_not_from_jar_uri(
    jar_env: str, endpoints: Callable[[list[str]], None], requests_log: Callable[..., list[str]]
) -> None:
    """Хост эндпоинта берётся из конфигов кластера, а не из authority URI jar'а.

    :param jar_env: фикстура, задающая ``OPENLINEAGE_JAR`` штатным значением стенда.
    :param endpoints: фикстура-настройщик списка эндпоинтов WebHDFS.
    :param requests_log: фикстура-настройщик ``urlopen``, возвращающая список запрошенных URL.
    :return: None.
    """
    endpoints(["http://other-host:50070"])
    urls = requests_log(lambda url: FakeResponse(200))

    assert ol_policy.jar_available(JAR, ol_policy.jar_path(JAR) or "") is True
    assert urls == [
        "http://other-host:50070/webhdfs/v1/opt/openlineage/openlineage-spark_2.13-1.46.0.jar?op=GETFILESTATUS"
    ]


# ---------------------------------------------------------------------------
# Слияние jar'ов и conf (инвариант 4)
# ---------------------------------------------------------------------------


def test_merge_jars_keeps_all_three_sources() -> None:
    """Три источника склеиваются в порядке jars → conf → наш, без дубликатов.

    :return: None.
    """
    assert ol_policy.merge_csv("a.jar", "b.jar,a.jar", JAR) == f"a.jar,b.jar,{JAR}"


def test_merge_jars_ignores_non_strings() -> None:
    """``None`` и не-строка дают пустой вклад.

    :return: None.
    """
    assert ol_policy.merge_csv(None, ["b.jar"], JAR) == JAR


@pytest.mark.parametrize(
    "templated",
    ["{{ params.jars | join(', ') }}", "{{ macros.pick('a.jar', 'b.jar') }}", "{% if x %}a.jar{% endif %}"],
)
def test_merge_jars_does_not_split_jinja(templated: str) -> None:
    """Значение с Jinja не режется по запятой: выражение осталось бы битым.

    :param templated: значение с Jinja-разметкой (параметризовано).
    :return: None.
    """
    assert ol_policy.merge_csv(templated, None, JAR) == f"{templated},{JAR}"
    assert ol_policy.merge_csv(None, templated, JAR) == f"{templated},{JAR}"


def test_dag_jars_survive(
    layout: SimpleNamespace,
    spark_operator: type,
    variable: Callable[..., SimpleNamespace],
    jar_ok: list[tuple[str, str]],
) -> None:
    """DAG задал ``jars=``: DAG-jar сохраняется в результате колбэка.

    :param layout: раскладка атрибутов conf/jars текущего провайдера (параметризована).
    :param spark_operator: фикстура, подменяющая распознавание ``SparkSubmitOperator`` дублём текущей раскладки.
    :param variable: фикстура, подменяющая чтение Variable ``openlineage_config``.
    :param jar_ok: фикстура, подменяющая зонд успешным ответом; список вызовов зонда.
    :return: None.
    """
    variable(raw=VALID_VARIABLE)
    task = make_task(layout, jars="a.jar")

    _run_callback(task)

    assert "a.jar" in getattr(task, layout.jars)


def test_conf_jars_move_into_jars_attribute(
    layout: SimpleNamespace,
    spark_operator: type,
    variable: Callable[..., SimpleNamespace],
    jar_ok: list[tuple[str, str]],
) -> None:
    """DAG задал только ``conf["spark.jars"]``: элементы уезжают в атрибут jars, ключ из conf удалён.

    Иначе jar-список был бы объявлен дважды и полагался бы на приоритет
    ``--jars`` над ``spark.jars`` у spark-submit.

    :param layout: раскладка атрибутов conf/jars текущего провайдера (параметризована).
    :param spark_operator: фикстура, подменяющая распознавание ``SparkSubmitOperator`` дублём текущей раскладки.
    :param variable: фикстура, подменяющая чтение Variable ``openlineage_config``.
    :param jar_ok: фикстура, подменяющая зонд успешным ответом; список вызовов зонда.
    :return: None.
    """
    variable(raw=VALID_VARIABLE)
    task = make_task(layout, conf={"spark.jars": "b.jar"})

    _run_callback(task)

    assert jars_of(task, layout) == "b.jar,hdfs:///jars/openlineage-spark.jar"
    assert "spark.jars" not in conf_of(task, layout)


def test_non_string_conf_jars_stay_in_conf(
    layout: SimpleNamespace,
    spark_operator: type,
    variable: Callable[..., SimpleNamespace],
    jar_ok: list[tuple[str, str]],
) -> None:
    """Нестроковый ``conf["spark.jars"]`` не мерджится и не удаляется: значение DAG'а не теряется молча.

    :param layout: раскладка атрибутов conf/jars текущего провайдера (параметризована).
    :param spark_operator: фикстура, подменяющая распознавание ``SparkSubmitOperator`` дублём текущей раскладки.
    :param variable: фикстура, подменяющая чтение Variable ``openlineage_config``.
    :param jar_ok: фикстура, подменяющая зонд успешным ответом; список вызовов зонда.
    :return: None.
    """
    variable(raw=VALID_VARIABLE)
    task = make_task(layout, conf={"spark.jars": ["b.jar"]})

    _run_callback(task)

    assert jars_of(task, layout) == "hdfs:///jars/openlineage-spark.jar"
    assert conf_of(task, layout)["spark.jars"] == ["b.jar"]


def test_both_jar_sources_are_merged(
    layout: SimpleNamespace,
    spark_operator: type,
    variable: Callable[..., SimpleNamespace],
    jar_ok: list[tuple[str, str]],
) -> None:
    """DAG задал и ``jars=``, и ``conf["spark.jars"]``: оба смерджены с нашим, без дубликатов.

    :param layout: раскладка атрибутов conf/jars текущего провайдера (параметризована).
    :param spark_operator: фикстура, подменяющая распознавание ``SparkSubmitOperator`` дублём текущей раскладки.
    :param variable: фикстура, подменяющая чтение Variable ``openlineage_config``.
    :param jar_ok: фикстура, подменяющая зонд успешным ответом; список вызовов зонда.
    :return: None.
    """
    variable(raw=VALID_VARIABLE)
    task = make_task(layout, jars="a.jar", conf={"spark.jars": "b.jar,a.jar"})

    _run_callback(task)

    assert jars_of(task, layout) == "a.jar,b.jar,hdfs:///jars/openlineage-spark.jar"


# ---------------------------------------------------------------------------
# inject_openlineage: парс не трогает conf/jars/DAG, не читает Variable и HDFS
# ---------------------------------------------------------------------------


def test_inject_does_not_touch_foreign_conf_keys(
    layout: SimpleNamespace,
    spark_operator: type,
    variable: Callable[..., SimpleNamespace],
    jar_ok: list[tuple[str, str]],
) -> None:
    """Ключи DAG-conf вне lineage-набора остаются как были после колбэка.

    :param layout: раскладка атрибутов conf/jars текущего провайдера (параметризована).
    :param spark_operator: фикстура, подменяющая распознавание ``SparkSubmitOperator`` дублём текущей раскладки.
    :param variable: фикстура, подменяющая чтение Variable ``openlineage_config``.
    :param jar_ok: фикстура, подменяющая зонд успешным ответом; список вызовов зонда.
    :return: None.
    """
    variable(raw=VALID_VARIABLE)
    dag = DummyDag()
    task = layout.cls(dag=dag, conf={"spark.app.name": "demo", "spark.executor.cores": "2"})

    _run_callback(task)

    conf = getattr(task, layout.conf)
    assert conf["spark.app.name"] == "demo"
    assert conf["spark.executor.cores"] == "2"


def test_inject_never_reads_variable(
    layout: SimpleNamespace, probe_forbidden: None, variable: Callable[..., SimpleNamespace]
) -> None:
    """Инвариант 6: парс не ходит в метастор.

    :param layout: раскладка атрибутов conf/jars текущего провайдера (параметризована).
    :param probe_forbidden: фикстура, которая валит тест при любом обращении к зонду.
    :param variable: фикстура, подменяющая чтение Variable ``openlineage_config``.
    :return: None.
    """
    state = variable(raw=VALID_VARIABLE)

    ol_policy.inject_openlineage(layout.cls(dag=DummyDag()))

    assert state.calls == 0


def test_inject_never_touches_network(layout: SimpleNamespace, probe_forbidden: None) -> None:
    """Инвариант 7: парс не делает сетевых вызовов.

    :param layout: раскладка атрибутов conf/jars текущего провайдера (параметризована).
    :param probe_forbidden: фикстура, которая валит тест при любом обращении к зонду.
    :return: None.
    """
    ol_policy.inject_openlineage(layout.cls(dag=DummyDag()))


def test_inject_ignores_openlineage_jar_env(
    layout: SimpleNamespace,
    spark_operator: type,
    variable: Callable[..., SimpleNamespace],
    jar_ok: list[tuple[str, str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """URI jar'а живёт в Variable; переменной окружения политика не знает.

    Зонд подменён фикстурой ``jar_ok``, а не запущен по-настоящему: реальный зонд
    зависит от ``HADOOP_CONF_DIR``/``YARN_CONF_DIR`` окружения, и в среде, где эти
    переменные указывают на настоящий кластер, тест иначе делал бы живые WebHDFS-запросы.

    :param layout: раскладка атрибутов conf/jars текущего провайдера (параметризована).
    :param spark_operator: фикстура, подменяющая распознавание ``SparkSubmitOperator`` дублём текущей раскладки.
    :param variable: фикстура, подменяющая чтение Variable ``openlineage_config``.
    :param jar_ok: фикстура, подменяющая зонд успешным ответом; список вызовов зонда.
    :param monkeypatch: фикстура подмены атрибутов и окружения.
    :return: None.
    """
    monkeypatch.setenv("OPENLINEAGE_JAR", "hdfs://namenode:9000/from-env.jar")
    variable(raw=VALID_VARIABLE)
    task = layout.cls(dag=DummyDag())

    _run_callback(task)

    assert "from-env.jar" not in (getattr(task, layout.jars) or "")
    assert all(jar_uri != "hdfs://namenode:9000/from-env.jar" for jar_uri, _ in jar_ok)


# ---------------------------------------------------------------------------
# Зонд jar в HDFS (§6, §6.1)
# ---------------------------------------------------------------------------


def test_probe_true_on_200(
    endpoints: Callable[[list[str]], None], requests_log: Callable[..., list[str]], caplog: pytest.LogCaptureFixture
) -> None:
    """200 — jar есть, перебор прекращается.

    :param endpoints: фикстура-настройщик списка эндпоинтов WebHDFS.
    :param requests_log: фикстура-настройщик ``urlopen``, возвращающая список запрошенных URL.
    :param caplog: фикстура pytest для перехвата записей лога.
    :return: None.
    """
    endpoints(["http://nn1:9870", "http://nn2:9870"])
    urls = requests_log(lambda url: FakeResponse(200))

    assert ol_policy.jar_available(JAR, "/opt/ol.jar") is True
    assert urls == ["http://nn1:9870/webhdfs/v1/opt/ol.jar?op=GETFILESTATUS"]
    assert warnings_of(caplog) == []


def test_probe_false_on_404_without_warning(
    endpoints: Callable[[list[str]], None], requests_log: Callable[..., list[str]], caplog: pytest.LogCaptureFixture
) -> None:
    """404 — штатное «jar не залит»: False и ни одного warning'а.

    :param endpoints: фикстура-настройщик списка эндпоинтов WebHDFS.
    :param requests_log: фикстура-настройщик ``urlopen``, возвращающая список запрошенных URL.
    :param caplog: фикстура pytest для перехвата записей лога.
    :return: None.
    """
    endpoints(["http://nn1:9870", "http://nn2:9870"])
    urls = requests_log(lambda url: HTTPError(url, 404, "Not Found", {}, None))

    assert ol_policy.jar_available(JAR, "/opt/ol.jar") is False
    assert len(urls) == 1
    assert warnings_of(caplog) == []


def test_probe_moves_to_next_endpoint_on_error(
    endpoints: Callable[[list[str]], None], requests_log: Callable[..., list[str]]
) -> None:
    """Сетевая ошибка — следующий эндпоинт.

    :param endpoints: фикстура-настройщик списка эндпоинтов WebHDFS.
    :param requests_log: фикстура-настройщик ``urlopen``, возвращающая список запрошенных URL.
    :return: None.
    """
    endpoints(["http://nn1:9870", "http://nn2:9870"])
    urls = requests_log(lambda url: URLError("boom") if "nn1" in url else FakeResponse(200))

    assert ol_policy.jar_available(JAR, "/opt/ol.jar") is True
    assert len(urls) == 2


def test_probe_warns_when_all_endpoints_fail(
    endpoints: Callable[[list[str]], None], requests_log: Callable[..., list[str]], caplog: pytest.LogCaptureFixture
) -> None:
    """Список кончился на сетевых ошибках — False с warning'ом про недоступность.

    :param endpoints: фикстура-настройщик списка эндпоинтов WebHDFS.
    :param requests_log: фикстура-настройщик ``urlopen``, возвращающая список запрошенных URL.
    :param caplog: фикстура pytest для перехвата записей лога.
    :return: None.
    """
    endpoints(["http://nn1:9870", "http://nn2:9870"])
    requests_log(lambda url: URLError("boom"))

    assert ol_policy.jar_available(JAR, "/opt/ol.jar") is False
    assert any("недоступны" in message for message in warnings_of(caplog))


def test_probe_treats_standby_as_next_endpoint(
    endpoints: Callable[[list[str]], None], requests_log: Callable[..., list[str]], caplog: pytest.LogCaptureFixture
) -> None:
    """403 + StandbyException — не отказ, а «спроси активный».

    :param endpoints: фикстура-настройщик списка эндпоинтов WebHDFS.
    :param requests_log: фикстура-настройщик ``urlopen``, возвращающая список запрошенных URL.
    :param caplog: фикстура pytest для перехвата записей лога.
    :return: None.
    """
    endpoints(["http://nn1:9870", "http://nn2:9870"])
    requests_log(lambda url: standby_error(url) if "nn1" in url else FakeResponse(200))

    assert ol_policy.jar_available(JAR, "/opt/ol.jar") is True
    assert warnings_of(caplog) == []


def test_probe_warns_distinctly_when_all_standby(
    endpoints: Callable[[list[str]], None], requests_log: Callable[..., list[str]], caplog: pytest.LogCaptureFixture
) -> None:
    """Все NameNode в standby — свой текст warning'а, отличимый от недоступности.

    :param endpoints: фикстура-настройщик списка эндпоинтов WebHDFS.
    :param requests_log: фикстура-настройщик ``urlopen``, возвращающая список запрошенных URL.
    :param caplog: фикстура pytest для перехвата записей лога.
    :return: None.
    """
    endpoints(["http://nn1:9870", "http://nn2:9870"])
    requests_log(standby_error)

    assert ol_policy.jar_available(JAR, "/opt/ol.jar") is False
    messages = warnings_of(caplog)
    assert any("standby" in message for message in messages)
    assert not any("недоступны" in message for message in messages)


def test_probe_warns_when_no_endpoints_resolved(
    endpoints: Callable[[list[str]], None], caplog: pytest.LogCaptureFixture
) -> None:
    """Пустой список эндпоинтов — отдельный исход со своим текстом.

    :param endpoints: фикстура-настройщик списка эндпоинтов WebHDFS.
    :param caplog: фикстура pytest для перехвата записей лога.
    :return: None.
    """
    endpoints([])

    assert ol_policy.jar_available(JAR, "/opt/ol.jar") is False
    assert any("не определены" in message for message in warnings_of(caplog))


def test_probe_warns_when_resolver_raises(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Битый или отсутствующий XML: False и warning про эндпоинты WebHDFS.

    :param monkeypatch: фикстура подмены атрибутов и окружения.
    :param caplog: фикстура pytest для перехвата записей лога.
    :return: None.
    """

    def _raise() -> list[str]:
        raise OSError("нет hdfs-site.xml")

    monkeypatch.setattr(ol_policy.probe, "resolve_webhdfs_urls", _raise)

    assert ol_policy.jar_available(JAR, "/opt/ol.jar") is False
    assert any("не удалось определить эндпоинты" in message for message in warnings_of(caplog))


def test_probe_401_retries_with_negotiate_header(
    monkeypatch: pytest.MonkeyPatch,
    endpoints: Callable[[list[str]], None],
    requests_log: Callable[[Callable[[object], object]], list[object]],
) -> None:
    """401 без auth → повтор того же URL с заголовком Authorization: Negotiate.

    :param monkeypatch: фикстура подмены атрибутов и окружения.
    :param endpoints: фикстура-настройщик списка эндпоинтов WebHDFS.
    :param requests_log: фикстура-настройщик ``urlopen``, возвращающая список запрошенных URL.
    :return: None.
    """
    endpoints(["http://nn1:9870"])
    _fake_spnego(monkeypatch, b"tok")

    def _handler(url: object) -> object:
        if isinstance(url, Request):
            assert url.get_header("Authorization") == "Negotiate " + base64.b64encode(b"tok").decode("ascii")
            return FakeResponse(200)
        return _http_error(401)

    urls = requests_log(_handler)
    assert ol_policy.probe._query_endpoint("http://nn1:9870", "/jars/ol.jar") == "found"
    assert len(urls) == 2


def test_probe_401_without_spnego_is_error_with_warning(
    monkeypatch: pytest.MonkeyPatch,
    endpoints: Callable[[list[str]], None],
    requests_log: Callable[[Callable[[object], object]], list[object]],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """SPNEGO недоступен (нет модуля) → исход error и warning про Kerberos.

    :param monkeypatch: фикстура подмены атрибутов и окружения.
    :param endpoints: фикстура-настройщик списка эндпоинтов WebHDFS.
    :param requests_log: фикстура-настройщик ``urlopen``, возвращающая список запрошенных URL.
    :param caplog: фикстура pytest для перехвата записей лога.
    :return: None.
    """
    monkeypatch.setitem(sys.modules, "spnego", None)
    requests_log(lambda url: _http_error(401))
    assert ol_policy.probe._query_endpoint("http://nn1:9870", "/jars/ol.jar") == "error"
    assert any("Kerberos" in message for message in warnings_of(caplog))


def test_probe_401_then_404_is_absent(
    monkeypatch: pytest.MonkeyPatch,
    requests_log: Callable[[Callable[[object], object]], list[object]],
) -> None:
    """Авторизованный повтор получил 404 → jar'а нет (absent, без warning'а).

    :param monkeypatch: фикстура подмены атрибутов и окружения.
    :param requests_log: фикстура-настройщик ``urlopen``, возвращающая список запрошенных URL.
    :return: None.
    """
    _fake_spnego(monkeypatch)

    def _handler(url: object) -> object:
        if isinstance(url, Request):
            return _http_error(404)
        return _http_error(401)

    requests_log(_handler)
    assert ol_policy.probe._query_endpoint("http://nn1:9870", "/jars/ol.jar") == "absent"


def test_probe_retries_endpoints_once_on_transient_errors(
    endpoints: Callable[[list[str]], None],
    requests_log: Callable[[Callable[[object], object]], list[object]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Первый проход — сплошные ошибки, второй находит jar: итог found, был sleep.

    :param endpoints: фикстура-настройщик списка эндпоинтов WebHDFS.
    :param requests_log: фикстура-настройщик ``urlopen``, возвращающая список запрошенных URL.
    :param monkeypatch: фикстура подмены атрибутов и окружения.
    :return: None.
    """
    endpoints(["http://nn1:9870"])
    pauses: list[float] = []
    monkeypatch.setattr(ol_policy.probe, "_sleep", pauses.append)
    attempts: list[object] = []

    def _handler(url: object) -> object:
        attempts.append(url)
        if len(attempts) == 1:
            return OSError("connection refused")
        return FakeResponse(200)

    requests_log(_handler)
    assert ol_policy.probe._probe("/jars/ol.jar") == "found"
    assert len(attempts) == 2
    assert pauses == [ol_policy.probe._RETRY_PAUSE_SEC]


def test_probe_deadline_covers_two_passes_over_ha_pair(
    endpoints: Callable[[list[str]], None],
    requests_log: Callable[[Callable[[object], object]], list[object]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """HA-пара NameNode: оба отказывают на первом проходе, второй эндпоинт находит jar на втором.

    Дедлайн зонда обязан пережить худший случай — 2 прохода x 2 эндпоинта, иначе
    ретрай, добавленный ради HA-кластера, не успевает сработать именно там, где нужен.

    :param endpoints: фикстура-настройщик списка эндпоинтов WebHDFS.
    :param requests_log: фикстура-настройщик ``urlopen``, возвращающая список запрошенных URL.
    :param monkeypatch: фикстура подмены атрибутов и окружения.
    :return: None.
    """
    endpoints(["http://nn1:9870", "http://nn2:9870"])
    pauses: list[float] = []
    monkeypatch.setattr(ol_policy.probe, "_sleep", pauses.append)
    attempts: list[object] = []

    def _handler(url: object) -> object:
        attempts.append(url)
        if len(attempts) <= 2:
            return OSError("connection refused")
        if len(attempts) == 3:
            return OSError("connection refused")
        return FakeResponse(200)

    requests_log(_handler)
    assert ol_policy.probe._probe("/jars/ol.jar") == "found"
    assert len(attempts) == 4
    assert pauses == [ol_policy.probe._RETRY_PAUSE_SEC]
    # Арифметика бюджета, а не только число попыток: 2 прохода x 2 эндпоинта x
    # 2 запроса (без токена + SPNEGO) x ENDPOINT_TIMEOUT_SEC + пауза ретрая
    # обязана укладываться в дедлайн, иначе правка ENDPOINT_TIMEOUT_SEC или
    # числа проходов молча вернёт дедлайн из-под HA-ретрая.
    assert ol_policy.probe._PROBE_DEADLINE_SEC >= (
        2 * 2 * 2 * ol_policy.probe.ENDPOINT_TIMEOUT_SEC + ol_policy.probe._RETRY_PAUSE_SEC
    )


def test_probe_absent_is_terminal_on_first_pass(
    endpoints: Callable[[list[str]], None],
    requests_log: Callable[[Callable[[object], object]], list[object]],
) -> None:
    """404 авторитетен: второго прохода нет.

    :param endpoints: фикстура-настройщик списка эндпоинтов WebHDFS.
    :param requests_log: фикстура-настройщик ``urlopen``, возвращающая список запрошенных URL.
    :return: None.
    """
    endpoints(["http://nn1:9870"])
    urls = requests_log(lambda url: _http_error(404))
    assert ol_policy.probe._probe("/jars/ol.jar") == "absent"
    assert len(urls) == 1


def test_probe_down_after_two_passes(
    endpoints: Callable[[list[str]], None],
    requests_log: Callable[[Callable[[object], object]], list[object]],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Оба прохода — ошибки: итог down, эндпоинт спрошен дважды, warning один.

    :param endpoints: фикстура-настройщик списка эндпоинтов WebHDFS.
    :param requests_log: фикстура-настройщик ``urlopen``, возвращающая список запрошенных URL.
    :param monkeypatch: фикстура подмены атрибутов и окружения.
    :param caplog: фикстура pytest для перехвата записей лога.
    :return: None.
    """
    endpoints(["http://nn1:9870"])
    monkeypatch.setattr(ol_policy.probe, "_sleep", lambda seconds: None)
    urls = requests_log(lambda url: OSError("refused"))
    assert ol_policy.probe._probe("/jars/ol.jar") == "down"
    assert len(urls) == 2
    assert any("недоступны" in message for message in warnings_of(caplog))


def test_probe_returns_within_deadline(
    monkeypatch: pytest.MonkeyPatch,
    endpoints: Callable[[list[str]], None],
    requests_log: Callable[..., list[str]],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Вызов возвращается не позже дедлайна, даже если висят все эндпоинты.

    :param monkeypatch: фикстура подмены атрибутов и окружения.
    :param endpoints: фикстура-настройщик списка эндпоинтов WebHDFS.
    :param requests_log: фикстура-настройщик ``urlopen``, возвращающая список запрошенных URL.
    :param caplog: фикстура pytest для перехвата записей лога.
    :return: None.
    """
    monkeypatch.setattr(ol_policy.probe, "_PROBE_DEADLINE_SEC", 0.2)
    endpoints([f"http://nn{index}:9870" for index in range(3)])

    def _hang(url: str) -> object:
        time.sleep(1.0)
        return FakeResponse(200)

    requests_log(_hang)

    started = time.monotonic()
    result = ol_policy.jar_available(JAR, "/opt/ol.jar")
    elapsed = time.monotonic() - started

    assert result is False
    assert elapsed < 2.0
    assert any("дедлайн" in message for message in warnings_of(caplog))


def test_probe_thread_is_daemon(monkeypatch: pytest.MonkeyPatch) -> None:
    """Перебор идёт в демон-потоке: пул потоков подвесил бы выход процесса.

    :param monkeypatch: фикстура подмены атрибутов и окружения.
    :return: None.
    """
    seen: list[bool] = []

    def _resolve() -> list[str]:
        seen.append(threading.current_thread().daemon)
        return []

    monkeypatch.setattr(ol_policy.probe, "resolve_webhdfs_urls", _resolve)
    ol_policy.jar_available(JAR, "/opt/ol.jar")

    assert seen == [True]


# ---------------------------------------------------------------------------
# Чтение Variable: read_config (§5.4)
# ---------------------------------------------------------------------------


def test_cfg_returns_dict(variable: Callable[..., SimpleNamespace]) -> None:
    """Валидный объект нового формата разбирается в словарь.

    :param variable: фикстура, подменяющая чтение Variable ``openlineage_config``.
    :return: None.
    """
    spark_conf = {
        "spark.extraListeners": "io.example.L",
        "spark.openlineage.transport.url": "http://marquez:5000",
        "spark.openlineage.namespace": "ns",
    }
    variable(raw=json.dumps({
        "enabled": True,
        "spark_conf": spark_conf,
        "openlineage_jar": "hdfs://namenode:9000/opt/ol.jar",
    }))

    assert ol_policy.variable.read_config() == {
        "enabled": True,
        "spark_conf": spark_conf,
        "openlineage_jar": "hdfs://namenode:9000/opt/ol.jar",
    }


def test_cfg_rejects_empty_object(variable: Callable[..., SimpleNamespace], caplog: pytest.LogCaptureFixture) -> None:
    """Пустой JSON-объект — не годная форма Variable.

    :param variable: фикстура, подменяющая чтение Variable ``openlineage_config``.
    :param caplog: фикстура pytest для перехвата записей лога.
    :return: None.
    """
    variable(raw="{}")

    assert ol_policy.variable.read_config() is None
    assert any("enabled (bool)" in message for message in warnings_of(caplog))


def test_cfg_rejects_old_shape(variable: Callable[..., SimpleNamespace], caplog: pytest.LogCaptureFixture) -> None:
    """Старый формат Variable ({enabled, url, namespace}) — не валиден.

    :param variable: фикстура, подменяющая чтение Variable ``openlineage_config``.
    :param caplog: фикстура pytest для перехвата записей лога.
    :return: None.
    """
    variable(raw='{"enabled": true, "url": "http://marquez:5000", "namespace": "ns"}')

    assert ol_policy.variable.read_config() is None
    messages = warnings_of(caplog)
    assert any("spark_conf" in message for message in messages)
    assert any("openlineage_jar" in message for message in messages)


@pytest.mark.parametrize(
    ("raw", "error", "marker"),
    [
        (None, None, "не задана"),
        (None, RuntimeError("db down"), "недоступна"),
        ("{not json", None, "JSON"),
        ('"строка"', None, "объект"),
        ("[1, 2]", None, "объект"),
        ("5", None, "объект"),
    ],
)
def test_cfg_returns_none_and_warns(
    variable: Callable[..., SimpleNamespace],
    caplog: pytest.LogCaptureFixture,
    raw: str | None,
    error: BaseException | None,
    marker: str,
) -> None:
    """Каждая причина отказа даёт ``None`` и свой warning, а не пустой лог.

    :param variable: фикстура, подменяющая чтение Variable ``openlineage_config``.
    :param caplog: фикстура pytest для перехвата записей лога.
    :param raw: сырое значение Variable для сценария (параметризовано).
    :param error: исключение, которое бросает чтение Variable (параметризовано).
    :param marker: ожидаемый маркер в тексте warning'а (параметризовано).
    :return: None.
    """
    variable(raw=raw, error=error)

    assert ol_policy.variable.read_config() is None
    messages = warnings_of(caplog)
    assert messages
    assert any(marker in message for message in messages)


def test_cfg_warns_about_auth(variable: Callable[..., SimpleNamespace], caplog: pytest.LogCaptureFixture) -> None:
    """Ключ ``auth`` распознаётся, чтобы отказать явно (инвариант 5).

    :param variable: фикстура, подменяющая чтение Variable ``openlineage_config``.
    :param caplog: фикстура pytest для перехвата записей лога.
    :return: None.
    """
    variable(raw=json.dumps({
        "enabled": True,
        "spark_conf": {
            "spark.extraListeners": "io.example.L",
            "spark.openlineage.transport.url": "http://marquez:5000",
            "spark.openlineage.namespace": "ns",
        },
        "openlineage_jar": "hdfs://n:9000/o.jar",
        "auth": {"token": "s3cr3t"},
    }))

    ol_policy.variable.read_config()

    assert any("auth" in message for message in warnings_of(caplog))
    assert not any("s3cr3t" in message for message in warnings_of(caplog))


def test_validate_cfg_aggregates_missing_fields(
    variable: Callable[..., SimpleNamespace], caplog: pytest.LogCaptureFixture
) -> None:
    """Частичный ``spark_conf`` и пустой ``openlineage_jar`` — один warning с обоими полями.

    :param variable: фикстура, подменяющая чтение Variable ``openlineage_config``.
    :param caplog: фикстура pytest для перехвата записей лога.
    :return: None.
    """
    variable(
        raw=json.dumps(
            {
                "enabled": True,
                "spark_conf": {"spark.extraListeners": "io.example.L"},
                "openlineage_jar": "",
            }
        )
    )

    assert ol_policy.variable.validate_config(ol_policy.variable.read_config()) is None

    messages = warnings_of(caplog)
    aggregated = [m for m in messages if "spark.openlineage.transport.url" in m and "openlineage_jar" in m]
    assert aggregated, f"ожидался агрегированный warning, получили: {messages}"


def test_jar_uris_splits_csv(variable: Callable[..., SimpleNamespace]) -> None:
    """Поле openlineage_jar разбирается как CSV из нескольких URI.

    :param variable: фикстура подмены Variable.
    :return: None.
    """
    variable(raw=json.dumps({
        "enabled": True,
        "spark_conf": {
            "spark.extraListeners": "io.openlineage.spark.agent.OpenLineageSparkListener",
            "spark.openlineage.transport.url": "http://marquez:5000",
            "spark.openlineage.namespace": "hadoop-cluster",
        },
        "openlineage_jar": "hdfs://nn:9000/a.jar, hdfs://nn:9000/b.jar",
    }))
    config = ol_policy.variable.validate_config(ol_policy.variable.read_config())
    assert config is not None
    assert config.jar_uris == ("hdfs://nn:9000/a.jar", "hdfs://nn:9000/b.jar")


def test_extra_conf_carries_unclaimed_spark_conf_keys(variable: Callable[..., SimpleNamespace]) -> None:
    """Ключи spark_conf сверх обязательных попадают в extra_conf.

    :param variable: фикстура подмены Variable.
    :return: None.
    """
    variable(raw=json.dumps({
        "enabled": True,
        "spark_conf": {
            "spark.extraListeners": "io.openlineage.spark.agent.OpenLineageSparkListener",
            "spark.openlineage.transport.url": "http://marquez:5000",
            "spark.openlineage.namespace": "hadoop-cluster",
            "spark.openlineage.dataset.namespaceResolvers.default.type": "normalize",
            "spark.master": "local[1]",
        },
        "openlineage_jar": "hdfs://nn:9000/a.jar",
    }))
    config = ol_policy.variable.validate_config(ol_policy.variable.read_config())
    assert config is not None
    assert config.extra_conf == {"spark.openlineage.dataset.namespaceResolvers.default.type": "normalize"}


def test_extra_conf_drops_spark_jars(variable: Callable[..., SimpleNamespace]) -> None:
    """``spark.jars`` в ``spark_conf`` Variable — управляемый ключ, в extra_conf не попадает.

    Регрессия для асимметрии с DAG-уровневым ``spark.jars``: тот колбэк осознанно вынимает из
    conf и мерджит в атрибут ``jars`` (см. ``callback._write_lineage``), а Variable-уровневый без
    этой записи в ``_MANAGED_KEYS`` проезжал бы в conf таски мимо мерджа.

    :param variable: фикстура подмены Variable.
    :return: None.
    """
    variable(raw=json.dumps({
        "enabled": True,
        "spark_conf": {
            "spark.extraListeners": "io.openlineage.spark.agent.OpenLineageSparkListener",
            "spark.openlineage.transport.url": "http://marquez:5000",
            "spark.openlineage.namespace": "hadoop-cluster",
            "spark.jars": "hdfs://nn:9000/sneaky.jar",
        },
        "openlineage_jar": "hdfs://nn:9000/a.jar",
    }))
    config = ol_policy.variable.validate_config(ol_policy.variable.read_config())
    assert config is not None
    assert config.extra_conf == {}


def test_variable_spark_jars_does_not_reach_task_conf(
    layout: SimpleNamespace,
    spark_operator: type,
    variable: Callable[..., SimpleNamespace],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``spark.jars`` из Variable ``spark_conf`` не доезжает до conf таски мимо мерджа jars.

    :param layout: раскладка атрибутов оператора.
    :param spark_operator: фикстура, подменяющая распознавание ``SparkSubmitOperator`` дублём текущей раскладки.
    :param variable: фикстура подмены Variable.
    :param monkeypatch: фикстура подмены.
    :return: None.
    """
    monkeypatch.setattr(ol_policy.probe, "jar_available", lambda jar_uri, path: True)
    variable(raw=json.dumps({
        "enabled": True,
        "spark_conf": {
            "spark.extraListeners": "io.openlineage.spark.agent.OpenLineageSparkListener",
            "spark.openlineage.transport.url": "http://marquez:5000",
            "spark.openlineage.namespace": "hadoop-cluster",
            "spark.jars": "hdfs://nn:9000/sneaky.jar",
        },
        "openlineage_jar": "hdfs://nn:9000/ol.jar",
    }))
    task = make_task(layout, jars="a.jar")
    ol_policy.ol_execute_callback({"task": task})
    conf = conf_of(task, layout)
    assert conf is not None
    assert "spark.jars" not in conf
    assert jars_of(task, layout) == "a.jar,hdfs://nn:9000/ol.jar"


def test_callback_reads_variable_once(
    layout: SimpleNamespace,
    spark_operator: type,
    variable: Callable[..., SimpleNamespace],
    jar_ok: list[tuple[str, str]],
) -> None:
    """Один запуск колбэка — один поход в метастор: значение передаётся вниз, а не перечитывается.

    :param layout: раскладка атрибутов conf/jars текущего провайдера (параметризована).
    :param spark_operator: фикстура, подменяющая распознавание ``SparkSubmitOperator`` дублём текущей раскладки.
    :param variable: фикстура, подменяющая чтение Variable ``openlineage_config``.
    :param jar_ok: фикстура, подменяющая зонд успешным ответом; список вызовов зонда.
    :return: None.
    """
    state = variable(raw=VALID_VARIABLE)

    _run_callback(layout.cls(dag=DummyDag(), conf={}))

    assert state.calls == 1


def test_variable_edit_is_picked_up_by_next_callback(
    layout: SimpleNamespace,
    spark_operator: type,
    variable: Callable[..., SimpleNamespace],
    jar_ok: list[tuple[str, str]],
) -> None:
    """Кэша нет: следующий запуск колбэка видит правку Variable сразу.

    :param layout: раскладка атрибутов conf/jars текущего провайдера (параметризована).
    :param spark_operator: фикстура, подменяющая распознавание ``SparkSubmitOperator`` дублём текущей раскладки.
    :param variable: фикстура, подменяющая чтение Variable ``openlineage_config``.
    :param jar_ok: фикстура, подменяющая зонд успешным ответом; список вызовов зонда.
    :return: None.
    """
    variable(raw=VALID_VARIABLE)
    first = layout.cls(dag=DummyDag(), conf={})
    _run_callback(first)
    assert getattr(first, layout.conf)["spark.openlineage.namespace"] == "stand"

    edited = json.loads(VALID_VARIABLE)
    edited["spark_conf"]["spark.openlineage.namespace"] = "edited"
    variable(raw=json.dumps(edited))
    second = layout.cls(dag=DummyDag(), conf={})
    _run_callback(second)
    assert getattr(second, layout.conf)["spark.openlineage.namespace"] == "edited"


def test_reset_state_resets_warn_dedup(monkeypatch: pytest.MonkeyPatch) -> None:
    """Агрегатор зовёт reset() логгера — единственного модуля с состоянием.

    :param monkeypatch: фикстура подмены атрибутов и окружения.
    :return: None.
    """
    called: list[str] = []
    monkeypatch.setattr(ol_policy.logger, "reset", lambda: called.append("logger"))
    ol_policy.reset_state()
    assert called == ["logger"]


def test_force_does_not_bypass_config_validation(
    layout: SimpleNamespace,
    spark_operator: type,
    variable: Callable[..., SimpleNamespace],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Форс переопределяет только ``enabled``: негодный url всё равно выключает лайнидж.

    :param layout: раскладка атрибутов conf/jars текущего провайдера (параметризована).
    :param spark_operator: фикстура, подменяющая распознавание ``SparkSubmitOperator`` дублём текущей раскладки.
    :param variable: фикстура, подменяющая чтение Variable ``openlineage_config``.
    :param caplog: фикстура pytest для перехвата записей лога.
    :return: None.
    """
    variable(
        raw=json.dumps(
            {
                "enabled": False,
                "spark_conf": {
                    "spark.extraListeners": "L",
                    "spark.openlineage.transport.url": "marquez:5000",
                    "spark.openlineage.namespace": "ns",
                },
                "openlineage_jar": JAR,
            }
        )
    )
    task = layout.cls(dag=DummyDag(), conf={}, params={"openlineage": True})

    _run_callback(task)

    assert getattr(task, layout.conf) == {}
    assert any("url" in message for message in warnings_of(caplog))


def test_listener_constant_is_gone() -> None:
    """Инвариант 12: класс listener'а не хардкодится в политике.

    :return: None.
    """
    assert not hasattr(ol_policy, "LISTENER")


# ---------------------------------------------------------------------------
# Гейт типа и инвариант 1 (apply_policy)
# ---------------------------------------------------------------------------


@pytest.fixture
def spark_operator(monkeypatch: pytest.MonkeyPatch, layout: SimpleNamespace) -> type:
    """Подменяет распознавание ``SparkSubmitOperator`` дублём текущей раскладки.

    :param monkeypatch: фикстура подмены.
    :param layout: раскладка атрибутов.
    :return: класс дубля оператора.
    """
    monkeypatch.setattr(ol_policy.operator, "_spark_submit_operator", lambda: layout.cls)
    return layout.cls


class NotSparkOperator:
    """Обычный не-Spark оператор."""

    template_fields = ()

    def __init__(self) -> None:
        """Создаёт дубль постороннего оператора.

        :return: None.
        """
        self.task_id = "bash"
        self.dag = DummyDag()
        self.params = {}


def test_non_spark_task_is_left_alone(
    spark_operator: type, jar_ok: list[tuple[str, str]], caplog: pytest.LogCaptureFixture
) -> None:
    """Не наша таска: тихий return без warning'а.

    :param spark_operator: фикстура, подменяющая распознавание ``SparkSubmitOperator`` дублём текущей раскладки.
    :param jar_ok: фикстура, подменяющая зонд успешным ответом; список вызовов зонда.
    :param caplog: фикстура pytest для перехвата записей лога.
    :return: None.
    """
    task = NotSparkOperator()
    before = dict(vars(task))

    ol_policy.apply_policy(task)

    assert vars(task) == before
    assert warnings_of(caplog) == []


def test_mapped_task_warns(
    spark_operator: type, jar_ok: list[tuple[str, str]], caplog: pytest.LogCaptureFixture
) -> None:
    """Динамический маппинг не поддерживается — но и не пропускается молча.

    :param spark_operator: фикстура, подменяющая распознавание ``SparkSubmitOperator`` дублём текущей раскладки.
    :param jar_ok: фикстура, подменяющая зонд успешным ответом; список вызовов зонда.
    :param caplog: фикстура pytest для перехвата записей лога.
    :return: None.
    """

    class Mapped:
        """Дубль ``MappedOperator``: Spark-овая по ``operator_class``, но не экземпляр."""

        template_fields = ()

        def __init__(self) -> None:
            """Создаёт дубль размапленной таски.

            :return: None.
            """
            self.task_id = "mapped"
            self.dag = DummyDag()
            self.params = {}
            self.operator_class = spark_operator

    task = Mapped()
    before = dict(vars(task))

    ol_policy.apply_policy(task)

    assert vars(task) == before
    assert any("маппинг" in message for message in warnings_of(caplog))


def test_spark_task_is_injected_through_apply_policy(layout: SimpleNamespace, spark_operator: type) -> None:
    """Гейт пропускает экземпляр оператора к дозаписи колбэка.

    :param layout: раскладка атрибутов conf/jars текущего провайдера (параметризована).
    :param spark_operator: фикстура, подменяющая распознавание ``SparkSubmitOperator`` дублём текущей раскладки.
    :return: None.
    """
    task = make_task(layout)

    ol_policy.apply_policy(task)

    assert task.on_execute_callback == [ol_policy.callback.ol_execute_callback]


def test_policy_survives_missing_provider(layout: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    """Провайдера нет — политике нечего делать, и она об этом не падает.

    :param layout: раскладка атрибутов conf/jars текущего провайдера (параметризована).
    :param monkeypatch: фикстура подмены атрибутов и окружения.
    :return: None.
    """
    monkeypatch.setattr(ol_policy.operator, "_spark_submit_operator", lambda: None)

    ol_policy.apply_policy(make_task(layout))


@pytest.mark.parametrize(
    ("params", "drop_dag"),
    [
        ({"openlineage": "yes"}, False),
        (RaisingParams({"openlineage": True}), False),
        (None, True),
        (None, False),
    ],
)
def test_policy_never_raises(
    layout: SimpleNamespace,
    spark_operator: type,
    params: object,
    drop_dag: bool,
) -> None:
    """Инвариант 1: политика не бросает на мусоре и не портит существующий conf.

    Сеть и переменная окружения больше не участвуют в парсе, поэтому «мусор»
    здесь — это только ``params`` и отсутствующий DAG. Там, где ни один гейт
    не блокирует таску, инжекция штатно проходит — это не считается сбоем.

    :param layout: раскладка атрибутов conf/jars текущего провайдера (параметризована).
    :param spark_operator: фикстура, подменяющая распознавание ``SparkSubmitOperator`` дублём текущей раскладки.
    :param params: значение ``params`` таски (параметризовано).
    :param drop_dag: убрать ли DAG у таски перед вызовом политики (параметризовано).
    :return: None.
    """
    task = make_task(layout, params=params, conf={"spark.app.name": "demo"})
    if drop_dag:
        task.dag = None

    ol_policy.apply_policy(task)

    assert conf_of(task, layout)["spark.app.name"] == "demo"
    if drop_dag:
        assert jars_of(task, layout) is None


def test_policy_survives_task_without_readable_ids(
    spark_operator: type, caplog: pytest.LogCaptureFixture
) -> None:
    """Даже нечитаемые dag_id/task_id не превращают warning в исключение.

    :param spark_operator: фикстура, подменяющая распознавание ``SparkSubmitOperator`` дублём текущей раскладки.
    :param caplog: фикстура pytest для перехвата записей лога.
    :return: None.
    """

    class Exploding:
        """Spark-овая по ``operator_class`` таска, чьи идентификаторы бросают."""

        template_fields = ()
        operator_class = spark_operator

        @property
        def task_id(self) -> str:
            """Идентификатор таски, чтение которого падает.

            :return: не возвращает.
            :raises RuntimeError: всегда.
            """
            raise RuntimeError("нечитаемый task_id")

        @property
        def dag(self) -> object:
            """DAG, чтение которого падает.

            :return: не возвращает.
            :raises RuntimeError: всегда.
            """
            raise RuntimeError("нечитаемый dag")

    ol_policy.apply_policy(Exploding())

    assert any("маппинг" in message for message in warnings_of(caplog))


def test_unexpected_error_is_swallowed_and_logged(
    layout: SimpleNamespace, spark_operator: type, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Непредвиденная ошибка гасится и попадает в лог, а не роняет импорт файла.

    :param layout: раскладка атрибутов conf/jars текущего провайдера (параметризована).
    :param spark_operator: фикстура, подменяющая распознавание ``SparkSubmitOperator`` дублём текущей раскладки.
    :param monkeypatch: фикстура подмены атрибутов и окружения.
    :param caplog: фикстура pytest для перехвата записей лога.
    :return: None.
    """

    def _boom(task: object) -> None:
        raise RuntimeError("неожиданно")

    monkeypatch.setattr(ol_policy.parse, "inject_openlineage", _boom)

    ol_policy.apply_policy(make_task(layout))

    assert any("cluster policy" in message for message in warnings_of(caplog))


# ---------------------------------------------------------------------------
# Проброс чужих исключений (инвариант 1, границы)
# ---------------------------------------------------------------------------


def test_passthrough_survives_missing_class(monkeypatch: pytest.MonkeyPatch) -> None:
    """Отсутствие ``AirflowClusterPolicySkipDag`` не обнуляет весь кортеж.

    :param monkeypatch: фикстура подмены атрибутов и окружения.
    :return: None.
    """
    created = install_airflow_exceptions(monkeypatch, ("AirflowTaskTimeout", "AirflowClusterPolicyViolation"))

    passthrough = ol_policy.passthrough_exceptions()

    assert set(passthrough) == {created.AirflowTaskTimeout, created.AirflowClusterPolicyViolation}


def test_passthrough_is_empty_without_airflow(monkeypatch: pytest.MonkeyPatch) -> None:
    """Airflow недоступен — кортеж пуст, и это легально.

    :param monkeypatch: фикстура подмены атрибутов и окружения.
    :return: None.
    """
    monkeypatch.setitem(sys.modules, "airflow.exceptions", None)

    assert ol_policy.passthrough_exceptions() == ()


@pytest.mark.parametrize("name", ["AirflowTaskTimeout", "AirflowClusterPolicyViolation", "AirflowClusterPolicySkipDag"])
def test_passthrough_exceptions_are_reraised(
    layout: SimpleNamespace, spark_operator: type, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    """Чужой механизм таймаута и чужое решение пропустить DAG политика не гасит.

    :param layout: раскладка атрибутов conf/jars текущего провайдера (параметризована).
    :param spark_operator: фикстура, подменяющая распознавание ``SparkSubmitOperator`` дублём текущей раскладки.
    :param monkeypatch: фикстура подмены атрибутов и окружения.
    :param name: имя класса исключения Airflow (параметризовано).
    :return: None.
    """
    created = install_airflow_exceptions(
        monkeypatch,
        ("AirflowTaskTimeout", "AirflowClusterPolicyViolation", "AirflowClusterPolicySkipDag"),
    )
    exception_class = getattr(created, name)

    def _boom(task: object) -> None:
        raise exception_class("наружу")

    monkeypatch.setattr(ol_policy.parse, "inject_openlineage", _boom)

    with pytest.raises(exception_class):
        ol_policy.apply_policy(make_task(layout))


def test_module_imports_without_airflow() -> None:
    """Модуль политики импортируется без Airflow: он не нужен ему на уровне модуля.

    :return: None.
    """
    module = importlib.reload(ol_policy)

    assert callable(module.ol_execute_callback)


# ---------------------------------------------------------------------------
# Инвариант 6: ноль обращений к метастору на парсе
# ---------------------------------------------------------------------------


def test_parse_never_touches_metastore(
    layout: SimpleNamespace,
    spark_operator: type,
    monkeypatch: pytest.MonkeyPatch,
    jar_ok: list[tuple[str, str]],
) -> None:
    """Ни ``Variable.get``, ни ``BaseHook.get_connection`` на этапе парсинга.

    :param layout: раскладка атрибутов conf/jars текущего провайдера (параметризована).
    :param spark_operator: фикстура, подменяющая распознавание ``SparkSubmitOperator`` дублём текущей раскладки.
    :param monkeypatch: фикстура подмены атрибутов и окружения.
    :param jar_ok: фикстура, подменяющая зонд успешным ответом; список вызовов зонда.
    :return: None.
    """

    class _Forbidden:
        """Дубль, который валит тест при любом обращении."""

        @staticmethod
        def get(*args: object, **kwargs: object) -> object:
            """Обращение к Variable на парсе запрещено.

            :param args: аргументы вызова.
            :param kwargs: именованные аргументы вызова.
            :return: не возвращает.
            """
            pytest.fail("Variable.get вызван на этапе парсинга")

        @staticmethod
        def get_connection(*args: object, **kwargs: object) -> object:
            """Обращение к Connection на парсе запрещено.

            :param args: аргументы вызова.
            :param kwargs: именованные аргументы вызова.
            :return: не возвращает.
            """
            pytest.fail("BaseHook.get_connection вызван на этапе парсинга")

    models = types.ModuleType("airflow.models")
    models.Variable = _Forbidden
    hooks = types.ModuleType("airflow.hooks.base")
    hooks.BaseHook = _Forbidden
    package = types.ModuleType("airflow")
    package.models = models
    monkeypatch.setitem(sys.modules, "airflow", package)
    monkeypatch.setitem(sys.modules, "airflow.models", models)
    monkeypatch.setitem(sys.modules, "airflow.hooks.base", hooks)

    ol_policy.apply_policy(make_task(layout))


# ---------------------------------------------------------------------------
# Сидинг Variable (§7)
# ---------------------------------------------------------------------------


# Литералы сидинга из airflow/scripts/start-airflow.sh: формат Variable и код
# политики обязаны сходиться, иначе стенд после рестарта тихо остаётся без лайниджа.
SEEDED_VARIABLE = json.dumps({
    "enabled": True,
    "spark_conf": {
        "spark.extraListeners": "io.openlineage.spark.agent.OpenLineageSparkListener",
        "spark.openlineage.transport.type": "http",
        "spark.openlineage.transport.url": "http://marquez:5000",
        "spark.openlineage.namespace": "hadoop-cluster",
        "spark.openlineage.columnLineage.datasetLineageEnabled": "true",
    },
    "openlineage_jar": "hdfs://namenode:9000/opt/openlineage/openlineage-spark_2.13-1.46.0.jar",
})


def test_seeded_value_parses_back_to_dict() -> None:
    """Значение сидинга — сырая строка JSON: обратный разбор даёт объект.

    :return: None.
    """
    assert isinstance(json.loads(SEEDED_VARIABLE), dict)


def test_seeded_value_is_accepted_by_the_policy(variable: Callable[..., SimpleNamespace]) -> None:
    """Регрессия §9: то, что сидит ``start-airflow.sh``, политика признаёт годным.

    Ровно на этом рассогласовании (сидинг нового формата против кода старого)
    стенд после рестарта попадал в ветку «конфиг негоден — лайниджа нет».

    :param variable: фикстура, подменяющая чтение Variable ``openlineage_config``.
    :return: None.
    """
    variable(raw=SEEDED_VARIABLE)

    assert ol_policy.variable.read_config() == json.loads(SEEDED_VARIABLE)
    assert ol_policy.variable.validate_config(ol_policy.variable.read_config()) is not None


def test_double_encoded_value_is_not_an_object() -> None:
    """Регрессия на ``--json``: повторная сериализация даёт строку, а не объект.

    :return: None.
    """
    seeded = json.dumps({"enabled": True, "url": "http://marquez:5000"})
    double_encoded = json.dumps(seeded, indent=2)

    assert not isinstance(json.loads(double_encoded), dict)


# ---------------------------------------------------------------------------
# merge_csv — listener'ы (§8, цикл 1)
# ---------------------------------------------------------------------------


def test_merge_listeners_dag_first_then_our() -> None:
    """DAG-listener первым, OL-listener последним, порядок CSV сохранён.

    :return: None.
    """
    assert ol_policy.merge_csv("com.example.A,com.example.B", "io.ol.L") == "com.example.A,com.example.B,io.ol.L"


def test_merge_listeners_dedups_existing_ol() -> None:
    """Если OL-listener уже в DAG-CSV — дедуп, не дублируется.

    :return: None.
    """
    assert ol_policy.merge_csv("com.example.A,io.ol.L", "io.ol.L") == "com.example.A,io.ol.L"


def test_merge_listeners_only_dag() -> None:
    """Только DAG — возвращаем DAG как есть.

    :return: None.
    """
    assert ol_policy.merge_csv("com.example.A", "") == "com.example.A"


def test_merge_listeners_only_our() -> None:
    """Только OL — возвращаем OL.

    :return: None.
    """
    assert ol_policy.merge_csv("", "io.ol.L") == "io.ol.L"


def test_merge_listeners_both_empty() -> None:
    """Пусто и там, и там — пустая строка.

    :return: None.
    """
    assert ol_policy.merge_csv("", "") == ""


@pytest.mark.parametrize("templated", ["{{ params.listener }}", "{% if x %}A,B{% endif %}"])
def test_merge_listeners_does_not_split_jinja(templated: str) -> None:
    """Jinja-выражение не режется по запятой.

    :param templated: значение с Jinja-разметкой (параметризовано).
    :return: None.
    """
    assert ol_policy.merge_csv(templated, "io.ol.L") == f"{templated},io.ol.L"
    assert ol_policy.merge_csv("", templated) == templated


# ---------------------------------------------------------------------------
# Различимость причин отказа Variable
# ---------------------------------------------------------------------------


def test_failure_reasons_are_pairwise_distinct(
    layout: SimpleNamespace,
    spark_operator: type,
    variable: Callable[..., SimpleNamespace],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Каждая причина отказа звучит в логе по-своему — иначе расследование слепое.

    :param layout: раскладка атрибутов conf/jars текущего провайдера (параметризована).
    :param spark_operator: фикстура, подменяющая распознавание ``SparkSubmitOperator`` дублём текущей раскладки.
    :param variable: фикстура, подменяющая чтение Variable ``openlineage_config``.
    :param caplog: фикстура pytest для перехвата записей лога.
    :return: None.
    """
    scenarios: dict[str, object] = {
        "no-var": None,
        "bad-json": "{",
        "not-object": "[1, 2, 3]",
        "bad-shape": '{"enabled": true}',
        "var-incomplete": json.dumps({"enabled": True, "spark_conf": {}, "openlineage_jar": ""}),
    }
    collected: list[str] = []
    for raw in scenarios.values():
        ol_policy.reset_state()
        caplog.clear()
        variable(raw=raw)
        _run_callback(layout.cls(dag=DummyDag(), conf={}))
        collected.extend(warnings_of(caplog))

    assert len(collected) == len(set(collected)), f"неразличимые тексты: {collected}"
    assert len(collected) == len(scenarios)


# ---------------------------------------------------------------------------
# Колбэк-фаза: ol_execute_callback / _inject / _write_lineage
# ---------------------------------------------------------------------------

VALID_VARIABLE = json.dumps({
    "enabled": True,
    "spark_conf": {
        "spark.extraListeners": "io.openlineage.spark.agent.OpenLineageSparkListener",
        "spark.openlineage.transport.url": "http://marquez:5000",
        "spark.openlineage.namespace": "stand",
    },
    "openlineage_jar": "hdfs:///jars/openlineage-spark.jar",
})


def _run_callback(task: object) -> None:
    """Зовёт колбэк политики так, как его зовёт Airflow: контекстом с таской.

    :param task: таска, которую нужно передать колбэку под ключом ``task``.
    :return: None.
    """
    callback.ol_execute_callback({"task": task})


def test_callback_injects_all_keys_on_success(
    layout: SimpleNamespace,
    spark_operator: type,
    variable: Callable[..., SimpleNamespace],
    jar_ok: list[tuple[str, str]],
) -> None:
    """Успех: пять ключей conf + jar в атрибуте jars, DAG-значения смерджены.

    :param layout: раскладка атрибутов conf/jars текущего провайдера (параметризована).
    :param spark_operator: фикстура, подменяющая распознавание ``SparkSubmitOperator`` дублём текущей раскладки.
    :param variable: фикстура, подменяющая чтение Variable ``openlineage_config``.
    :param jar_ok: фикстура, подменяющая зонд успешным ответом; список вызовов зонда.
    :return: None.
    """
    variable(raw=json.dumps({
        "enabled": True,
        "spark_conf": {
            "spark.extraListeners": "io.openlineage.spark.agent.OpenLineageSparkListener",
            "spark.openlineage.transport.url": "http://marquez:5000",
            "spark.openlineage.namespace": "stand",
            "spark.openlineage.columnLineage.datasetLineageEnabled": "true",
        },
        "openlineage_jar": "hdfs:///jars/openlineage-spark.jar",
    }))
    task = layout.cls(dag=DummyDag(), conf={"spark.executor.cores": "2"}, jars="hdfs:///user/app.jar")

    _run_callback(task)

    conf = getattr(task, layout.conf)
    assert conf["spark.extraListeners"] == "io.openlineage.spark.agent.OpenLineageSparkListener"
    assert conf["spark.openlineage.transport.type"] == "http"
    assert conf["spark.openlineage.transport.url"] == "http://marquez:5000"
    assert conf["spark.openlineage.namespace"] == "stand"
    assert conf["spark.openlineage.columnLineage.datasetLineageEnabled"] == "true"
    assert conf["spark.executor.cores"] == "2"
    assert getattr(task, layout.jars) == "hdfs:///user/app.jar,hdfs:///jars/openlineage-spark.jar"


def test_callback_merges_dag_listener_and_quotes(
    layout: SimpleNamespace,
    spark_operator: type,
    variable: Callable[..., SimpleNamespace],
    jar_ok: list[tuple[str, str]],
) -> None:
    """DAG-значение с кавычкой (раньше нелитерализуемое) мерджится как обычная строка.

    :param layout: раскладка атрибутов conf/jars текущего провайдера (параметризована).
    :param spark_operator: фикстура, подменяющая распознавание ``SparkSubmitOperator`` дублём текущей раскладки.
    :param variable: фикстура, подменяющая чтение Variable ``openlineage_config``.
    :param jar_ok: фикстура, подменяющая зонд успешным ответом; список вызовов зонда.
    :return: None.
    """
    variable(raw=VALID_VARIABLE)
    task = layout.cls(dag=DummyDag(), conf={"spark.extraListeners": 'com.x."Weird"Listener'})

    _run_callback(task)

    assert getattr(task, layout.conf)["spark.extraListeners"] == (
        'com.x."Weird"Listener,io.openlineage.spark.agent.OpenLineageSparkListener'
    )


def test_callback_refusal_leaves_task_untouched(
    layout: SimpleNamespace,
    spark_operator: type,
    variable: Callable[..., SimpleNamespace],
    probe_forbidden: None,
) -> None:
    """enabled=false → conf и jars байт-в-байт как были, зонд не звался.

    :param layout: раскладка атрибутов conf/jars текущего провайдера (параметризована).
    :param spark_operator: фикстура, подменяющая распознавание ``SparkSubmitOperator`` дублём текущей раскладки.
    :param variable: фикстура, подменяющая чтение Variable ``openlineage_config``.
    :param probe_forbidden: фикстура, которая валит тест при любом обращении к зонду.
    :return: None.
    """
    variable(raw=json.dumps({"enabled": False, "spark_conf": {}, "openlineage_jar": "hdfs:///x.jar"}))
    conf_before = {"spark.executor.cores": "2"}
    task = layout.cls(dag=DummyDag(), conf=dict(conf_before), jars="a.jar")

    _run_callback(task)

    assert getattr(task, layout.conf) == conf_before
    assert getattr(task, layout.jars) == "a.jar"


def test_callback_refusal_on_missing_jar(
    layout: SimpleNamespace,
    spark_operator: type,
    variable: Callable[..., SimpleNamespace],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Зонд не подтвердил jar → ни одного ключа не появилось, причина в логе.

    :param layout: раскладка атрибутов conf/jars текущего провайдера (параметризована).
    :param spark_operator: фикстура, подменяющая распознавание ``SparkSubmitOperator`` дублём текущей раскладки.
    :param variable: фикстура, подменяющая чтение Variable ``openlineage_config``.
    :param monkeypatch: фикстура подмены атрибутов и окружения.
    :param caplog: фикстура pytest для перехвата записей лога.
    :return: None.
    """
    variable(raw=VALID_VARIABLE)
    monkeypatch.setattr(ol_policy.probe, "jar_available", lambda jar_uri, path: False)
    task = layout.cls(dag=DummyDag(), conf={})

    _run_callback(task)

    assert getattr(task, layout.conf) == {}
    assert getattr(task, layout.jars) is None


@pytest.mark.parametrize("bad_url", ['""', '"   "', "5000", '"marquez:5000"', "null"])
def test_callback_rejects_bad_url(
    layout: SimpleNamespace,
    spark_operator: type,
    variable: Callable[..., SimpleNamespace],
    caplog: pytest.LogCaptureFixture,
    bad_url: str,
) -> None:
    """Негодный ``url`` в spark_conf выключает лайнидж целиком: таска остаётся нетронутой.

    Регрессия для параметризованного покрытия URL-валидации ``variable.validate_config``,
    ранее закрытого удалённым ``test_macro_rejects_bad_url`` (макро-эра инъекции).

    :param layout: раскладка атрибутов conf/jars текущего провайдера (параметризована).
    :param spark_operator: фикстура, подменяющая распознавание ``SparkSubmitOperator`` дублём текущей раскладки.
    :param variable: фикстура, подменяющая чтение Variable ``openlineage_config``.
    :param caplog: фикстура pytest для перехвата записей лога.
    :param bad_url: негодное значение ``url`` (параметризовано).
    :return: None.
    """
    variable(
        raw=(
            '{"enabled": true, "spark_conf": '
            '{"spark.extraListeners": "io.openlineage.spark.agent.OpenLineageSparkListener", '
            f'"spark.openlineage.transport.url": {bad_url}, '
            '"spark.openlineage.namespace": "stand"}, '
            '"openlineage_jar": "hdfs:///jars/openlineage-spark.jar"}'
        )
    )
    conf_before = {"spark.executor.cores": "2"}
    task = layout.cls(dag=DummyDag(), conf=dict(conf_before), jars="a.jar")

    _run_callback(task)

    assert getattr(task, layout.conf) == conf_before
    assert getattr(task, layout.jars) == "a.jar"
    assert any("url" in message for message in warnings_of(caplog))


@pytest.mark.parametrize("bad_namespace", ['""', '"   "', "5000", "null"])
def test_callback_rejects_bad_namespace(
    layout: SimpleNamespace,
    spark_operator: type,
    variable: Callable[..., SimpleNamespace],
    caplog: pytest.LogCaptureFixture,
    bad_namespace: str,
) -> None:
    """Негодный ``namespace`` в spark_conf выключает лайнидж целиком: таска остаётся нетронутой.

    Регрессия для параметризованного покрытия namespace-валидации ``variable.validate_config``,
    ранее закрытого удалённым ``test_macro_rejects_bad_namespace`` (макро-эра инъекции).

    :param layout: раскладка атрибутов conf/jars текущего провайдера (параметризована).
    :param spark_operator: фикстура, подменяющая распознавание ``SparkSubmitOperator`` дублём текущей раскладки.
    :param variable: фикстура, подменяющая чтение Variable ``openlineage_config``.
    :param caplog: фикстура pytest для перехвата записей лога.
    :param bad_namespace: негодное значение ``namespace`` (параметризовано).
    :return: None.
    """
    variable(
        raw=(
            '{"enabled": true, "spark_conf": '
            '{"spark.extraListeners": "io.openlineage.spark.agent.OpenLineageSparkListener", '
            '"spark.openlineage.transport.url": "http://marquez:5000", '
            f'"spark.openlineage.namespace": {bad_namespace}' + "}, "
            '"openlineage_jar": "hdfs:///jars/openlineage-spark.jar"}'
        )
    )
    conf_before = {"spark.executor.cores": "2"}
    task = layout.cls(dag=DummyDag(), conf=dict(conf_before), jars="a.jar")

    _run_callback(task)

    assert getattr(task, layout.conf) == conf_before
    assert getattr(task, layout.jars) == "a.jar"
    assert any("namespace" in message for message in warnings_of(caplog))


def test_extra_conf_reaches_task_conf(
    layout: SimpleNamespace,
    spark_operator: type,
    variable: Callable[..., SimpleNamespace],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ключ резолвера из spark_conf доезжает до conf таски.

    :param layout: раскладка атрибутов оператора.
    :param spark_operator: фикстура, подменяющая распознавание ``SparkSubmitOperator`` дублём текущей раскладки.
    :param variable: фикстура подмены Variable.
    :param monkeypatch: фикстура подмены.
    :return: None.
    """
    monkeypatch.setattr(ol_policy.probe, "jar_available", lambda jar_uri, path: True)
    variable(raw=json.dumps({
        "enabled": True,
        "spark_conf": {
            "spark.extraListeners": "io.openlineage.spark.agent.OpenLineageSparkListener",
            "spark.openlineage.transport.url": "http://marquez:5000",
            "spark.openlineage.namespace": "hadoop-cluster",
            "spark.openlineage.dataset.namespaceResolvers.default.type": "normalize",
        },
        "openlineage_jar": "hdfs://nn:9000/ol.jar,hdfs://nn:9000/resolver.jar",
    }))
    task = make_task(layout)
    ol_policy.ol_execute_callback({"task": task})
    conf = conf_of(task, layout)
    assert conf is not None
    assert conf["spark.openlineage.dataset.namespaceResolvers.default.type"] == "normalize"
    assert jars_of(task, layout) == "hdfs://nn:9000/ol.jar,hdfs://nn:9000/resolver.jar"


def test_every_jar_uri_is_probed(
    layout: SimpleNamespace,
    spark_operator: type,
    variable: Callable[..., SimpleNamespace],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Зонд вызывается для каждого URI из CSV, отсутствие любого гасит лайнидж.

    :param layout: раскладка атрибутов оператора.
    :param spark_operator: фикстура, подменяющая распознавание ``SparkSubmitOperator`` дублём текущей раскладки.
    :param variable: фикстура подмены Variable.
    :param monkeypatch: фикстура подмены.
    :return: None.
    """
    probed: list[str] = []

    def _available(jar_uri: str, path: str) -> bool:
        """Дубль зонда: помнит запрошенные URI, второй объявляет отсутствующим.

        :param jar_uri: URI jar'а.
        :param path: путь внутри HDFS.
        :return: True для первого URI, False для остальных.
        """
        probed.append(jar_uri)
        return len(probed) == 1

    monkeypatch.setattr(ol_policy.probe, "jar_available", _available)
    variable(raw=json.dumps({
        "enabled": True,
        "spark_conf": {
            "spark.extraListeners": "io.openlineage.spark.agent.OpenLineageSparkListener",
            "spark.openlineage.transport.url": "http://marquez:5000",
            "spark.openlineage.namespace": "hadoop-cluster",
        },
        "openlineage_jar": "hdfs://nn:9000/ol.jar,hdfs://nn:9000/resolver.jar",
    }))
    task = make_task(layout)
    ol_policy.ol_execute_callback({"task": task})
    assert probed == ["hdfs://nn:9000/ol.jar", "hdfs://nn:9000/resolver.jar"]
    assert jars_of(task, layout) is None


def test_callback_url_overrides_dag_value_with_log(
    layout: SimpleNamespace,
    spark_operator: type,
    variable: Callable[..., SimpleNamespace],
    jar_ok: list[tuple[str, str]],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """DAG задал transport.url — OL-значение побеждает, конфликт залогирован info.

    :param layout: раскладка атрибутов conf/jars текущего провайдера (параметризована).
    :param spark_operator: фикстура, подменяющая распознавание ``SparkSubmitOperator`` дублём текущей раскладки.
    :param variable: фикстура, подменяющая чтение Variable ``openlineage_config``.
    :param jar_ok: фикстура, подменяющая зонд успешным ответом; список вызовов зонда.
    :param caplog: фикстура pytest для перехвата записей лога.
    :return: None.
    """
    caplog.set_level(logging.INFO)
    variable(raw=VALID_VARIABLE)
    task = layout.cls(dag=DummyDag(), conf={"spark.openlineage.transport.url": "http://other:1"})

    _run_callback(task)

    assert getattr(task, layout.conf)["spark.openlineage.transport.url"] == "http://marquez:5000"
    assert any("переопределяется" in r.getMessage() for r in caplog.records)


def test_callback_never_raises(
    layout: SimpleNamespace, spark_operator: type, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Внутренний сбой гасится warning'ом, наружу ничего не летит.

    :param layout: раскладка атрибутов conf/jars текущего провайдера (параметризована).
    :param spark_operator: фикстура, подменяющая распознавание ``SparkSubmitOperator`` дублём текущей раскладки.
    :param monkeypatch: фикстура подмены атрибутов и окружения.
    :return: None.
    """
    monkeypatch.setattr(ol_policy.variable, "read_config", lambda: (_ for _ in ()).throw(RuntimeError("boom")))

    _run_callback(layout.cls(dag=DummyDag(), conf={}))  # не бросает


def test_callback_ignores_context_without_task() -> None:
    """Контекст без таски (или чужой объект) — тихий выход.

    :return: None.
    """
    callback.ol_execute_callback({})


def test_callback_force_on_beats_disabled(
    layout: SimpleNamespace,
    spark_operator: type,
    variable: Callable[..., SimpleNamespace],
    jar_ok: list[tuple[str, str]],
) -> None:
    """Форс таски включает лайнидж, даже если Variable.enabled=false (см. test_force_enables_without_enabled_flag).

    :param layout: раскладка атрибутов conf/jars текущего провайдера (параметризована).
    :param spark_operator: фикстура, подменяющая распознавание ``SparkSubmitOperator`` дублём текущей раскладки.
    :param variable: фикстура, подменяющая чтение Variable ``openlineage_config``.
    :param jar_ok: фикстура, подменяющая зонд успешным ответом; список вызовов зонда.
    :return: None.
    """
    variable(raw=json.dumps({
        "enabled": False,
        "spark_conf": {
            "spark.extraListeners": "io.openlineage.spark.agent.OpenLineageSparkListener",
            "spark.openlineage.transport.url": "http://marquez:5000",
            "spark.openlineage.namespace": "stand",
        },
        "openlineage_jar": "hdfs:///jars/openlineage-spark.jar",
    }))
    task = layout.cls(dag=DummyDag(), conf={}, params={"openlineage": True})

    _run_callback(task)

    assert getattr(task, layout.conf)["spark.extraListeners"] == "io.openlineage.spark.agent.OpenLineageSparkListener"


def test_callback_config_values_never_reach_the_log(
    layout: SimpleNamespace,
    spark_operator: type,
    variable: Callable[..., SimpleNamespace],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Инвариант 5 на колбэк-пути: listener/url/namespace из Variable не попадают в warning-лог.

    Зонд отказывает (jar_available=False), поэтому отказ логируется через ``warn_once``
    с ``jar_uri`` — это допустимо (см. ``probe.py``: "probe-deadline", "probe-error"), а
    вот listener/url/namespace из уже провалидированного ``config`` светиться в warning
    не должны вовсе.

    :param layout: раскладка атрибутов conf/jars текущего провайдера (параметризована).
    :param spark_operator: фикстура, подменяющая распознавание ``SparkSubmitOperator`` дублём текущей раскладки.
    :param variable: фикстура, подменяющая чтение Variable ``openlineage_config``.
    :param monkeypatch: фикстура подмены атрибутов и окружения.
    :param caplog: фикстура pytest для перехвата записей лога.
    :return: None.
    """
    variable(raw=json.dumps({
        "enabled": True,
        "spark_conf": {
            "spark.extraListeners": "com.example.SecretListener",
            "spark.openlineage.transport.url": "http://secret-host:5000",
            "spark.openlineage.namespace": "secret-namespace",
        },
        "openlineage_jar": "hdfs:///jars/openlineage-spark.jar",
    }))
    monkeypatch.setattr(ol_policy.probe, "jar_available", lambda jar_uri, path: False)
    task = layout.cls(dag=DummyDag(), conf={})

    _run_callback(task)

    joined = "\n".join(warnings_of(caplog))
    assert "com.example.SecretListener" not in joined
    assert "secret-host" not in joined
    assert "secret-namespace" not in joined


