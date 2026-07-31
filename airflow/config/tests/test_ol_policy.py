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
from conftest import DummyDag, PublicLayoutOperator, warnings_of

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
        def _urlopen(url: object, timeout: float | None = None) -> object:
            urls.append(url)
            result = handler(url)
            if isinstance(result, BaseException):
                raise result
            return result

        monkeypatch.setattr(ol_policy.probe, "urlopen", _urlopen)
        return urls

    return _install


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """Подменяет модульный источник времени политики управляемым счётчиком.

    Оба TTL-мемо пакета (зонд jar'а и конфиг Variable) реэкспортируют
    ``utils.now`` под именем ``_now`` — фикстура подменяет обе точки одним
    и тем же счётчиком.

    :param monkeypatch: фикстура подмены.
    :return: объект с полем ``now``, которое тест двигает вперёд.
    """
    state = SimpleNamespace(now=1000.0)
    monkeypatch.setattr(ol_policy.probe, "_now", lambda: state.now)
    monkeypatch.setattr(ol_policy.variable, "_now", lambda: state.now)
    return state


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
# Выбор канала для DAG-значения и сборка вызова макроса
# ---------------------------------------------------------------------------


def test_dag_channel_empty_value() -> None:
    """Пусто, None и пробелы — DAG молчал: префикса нет, канал ''."""
    assert ol_policy.parse._dag_channel(None) == ("", "")
    assert ol_policy.parse._dag_channel("") == ("", "")
    assert ol_policy.parse._dag_channel("   ") == ("", "")


def test_dag_channel_safe_literal() -> None:
    """Безопасное значение уходит литералом, префикса нет."""
    assert ol_policy.parse._dag_channel("a.jar,b.jar") == ("", "a.jar,b.jar")


@pytest.mark.parametrize("value", ["{{ params.jars }}", "{% if x %}a.jar{% endif %}", "it's.jar", 'say"hi".jar', r"C:\new.jar"])
def test_dag_channel_unsafe_value_stays_in_the_string(value: str) -> None:
    """Jinja и кавычки нельзя вложить в текст вызова макроса — значение остаётся слева."""
    assert ol_policy.parse._dag_channel(value) == (value, None)


def test_macro_call_renders_literal() -> None:
    """Литерал попадает в вызов в одинарных кавычках."""
    call = ol_policy.parse._macro_call("listener", "none", "com.example.A")

    assert call == "{{ __openlineage_v1('listener', none, 'com.example.A') }}"


def test_macro_call_renders_none_channel() -> None:
    """Канал None рендерится как Jinja-литерал none, а не как строка 'None'."""
    assert ol_policy.parse._macro_call("jar", "true", None) == "{{ __openlineage_v1('jar', true, none) }}"


def test_macro_call_renders_empty_channel() -> None:
    """Канал '' рендерится пустой строкой-литералом."""
    assert ol_policy.parse._macro_call("url", "none", "") == "{{ __openlineage_v1('url', none, '') }}"


# ---------------------------------------------------------------------------
# Раскладка атрибутов оператора (инвариант 8)
# ---------------------------------------------------------------------------


def test_operator_attrs_resolves_layout(layout: SimpleNamespace) -> None:
    """Имена conf/jars берутся по факту, а не зашиты."""
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
    """Незнакомая раскладка: warning и ни одного созданного атрибута."""
    task = broken()
    before = dict(vars(task))

    ol_policy.inject_openlineage(task)

    assert vars(task) == before
    assert any("раскладка" in message for message in warnings_of(caplog))


# ---------------------------------------------------------------------------
# Тумблер из DAG'а: таблица истинности §5.3, парсовая половина
# ---------------------------------------------------------------------------


def test_task_force_off_is_silent(
    layout: SimpleNamespace, probe_forbidden: None, caplog: pytest.LogCaptureFixture
) -> None:
    """Форс-выключение таски: тихий отказ без единой записи в лог."""
    task = make_task(layout, params={"openlineage": False})

    ol_policy.inject_openlineage(task)

    assert conf_of(task, layout) is None
    assert warnings_of(caplog) == []


def test_dag_force_off_is_silent(
    layout: SimpleNamespace, probe_forbidden: None, caplog: pytest.LogCaptureFixture
) -> None:
    """Форс-выключение на уровне DAG'а действует так же, как на уровне таски."""
    task = make_task(layout, dag=DummyDag(params={"openlineage": False}))

    ol_policy.inject_openlineage(task)

    assert conf_of(task, layout) is None
    assert warnings_of(caplog) == []


def test_task_force_on_beats_dag_force_off(
    layout: SimpleNamespace, jar_ok: list[tuple[str, str]], caplog: pytest.LogCaptureFixture
) -> None:
    """Форс таски перекрывает форс DAG'а: в шаблон уезжает ``true``."""
    task = make_task(
        layout,
        dag=DummyDag(params={"openlineage": False}),
        params={"openlineage": True},
    )

    ol_policy.inject_openlineage(task)

    assert "'listener', true" in conf_of(task, layout)["spark.extraListeners"]
    assert warnings_of(caplog) == []


def test_dag_force_on_is_used_when_task_is_silent(
    layout: SimpleNamespace, jar_ok: list[tuple[str, str]], caplog: pytest.LogCaptureFixture
) -> None:
    """Форс-включение DAG'а действует, если таска не высказалась."""
    task = make_task(layout, dag=DummyDag(params={"openlineage": True}))

    ol_policy.inject_openlineage(task)

    assert conf_of(task, layout)["spark.extraListeners"] == ol_policy.parse._macro_call("listener", "true", "")
    assert warnings_of(caplog) == []


def test_missing_toggle_is_neutral_and_silent(
    layout: SimpleNamespace, jar_ok: list[tuple[str, str]], caplog: pytest.LogCaptureFixture
) -> None:
    """Ключа нет ни у таски, ни у DAG'а: решение уходит в Variable, лог пуст."""
    task = make_task(layout)

    ol_policy.inject_openlineage(task)

    assert conf_of(task, layout)["spark.extraListeners"] == ol_policy.parse._macro_call("listener", "none", "")
    assert warnings_of(caplog) == []


def test_none_toggle_is_neutral_and_silent(
    layout: SimpleNamespace, jar_ok: list[tuple[str, str]], caplog: pytest.LogCaptureFixture
) -> None:
    """Объявленный нейтральный ``None`` молчит так же, как отсутствие ключа."""
    task = make_task(layout, params={"openlineage": None}, dag=DummyDag(params={"openlineage": None}))

    ol_policy.inject_openlineage(task)

    assert conf_of(task, layout)["spark.extraListeners"] == ol_policy.parse._macro_call("listener", "none", "")
    assert warnings_of(caplog) == []


@pytest.mark.parametrize("value", ["yes", 1, [], {}])
def test_non_bool_toggle_warns_and_falls_through(
    layout: SimpleNamespace, jar_ok: list[tuple[str, str]], caplog: pytest.LogCaptureFixture, value: object
) -> None:
    """Негодное значение тумблера игнорируется с warning'ом, решение уходит ниже."""
    task = make_task(layout, params={"openlineage": value})

    ol_policy.inject_openlineage(task)

    assert conf_of(task, layout)["spark.extraListeners"] == ol_policy.parse._macro_call("listener", "none", "")
    assert any("openlineage" in message for message in warnings_of(caplog))


def test_non_bool_task_toggle_does_not_hide_dag_force_off(
    layout: SimpleNamespace, probe_forbidden: None, caplog: pytest.LogCaptureFixture
) -> None:
    """Негодный уровень игнорируется целиком: решает следующий уровень лесенки."""
    task = make_task(layout, params={"openlineage": "yes"}, dag=DummyDag(params={"openlineage": False}))

    ol_policy.inject_openlineage(task)

    assert conf_of(task, layout) is None


class RaisingParams(dict):
    """``params``, чьё чтение значения бросает — как ``ParamValidationError``."""

    def __getitem__(self, key: str) -> object:
        """Всегда бросает при чтении значения.

        :param key: имя параметра.
        :return: не возвращает.
        :raises ValueError: всегда.
        """
        raise ValueError("param validation failed")


def test_raising_params_warns_and_does_not_break(
    layout: SimpleNamespace, jar_ok: list[tuple[str, str]], caplog: pytest.LogCaptureFixture
) -> None:
    """Исключение при чтении ``params`` гасится: уровень игнорируется с warning'ом."""
    task = make_task(layout, params=RaisingParams({"openlineage": True}))

    ol_policy.inject_openlineage(task)

    assert conf_of(task, layout)["spark.extraListeners"] == ol_policy.parse._macro_call("listener", "none", "")
    assert any("params" in message for message in warnings_of(caplog))


def test_lineage_forced_returns_only_tristate(layout: SimpleNamespace, caplog: pytest.LogCaptureFixture) -> None:
    """Наружу негодное значение тумблера не отдаётся никогда."""
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
    """Форс-выключение сильнее гейта чужого листенера: лог пуст."""
    task = make_task(
        layout,
        conf={"spark.extraListeners": FOREIGN_LISTENER},
        params={"openlineage": False},
    )

    ol_policy.inject_openlineage(task)

    assert conf_of(task, layout) == {"spark.extraListeners": FOREIGN_LISTENER}
    assert warnings_of(caplog) == []


# ---------------------------------------------------------------------------
# Макрос в user_defined_macros
# ---------------------------------------------------------------------------


def test_macro_is_added_without_dropping_others(layout: SimpleNamespace, jar_ok: list[tuple[str, str]]) -> None:
    """Чужие макросы DAG'а сохраняются, наш добавляется рядом."""

    def _other() -> str:
        """Чужой макрос DAG'а.

        :return: строка-заглушка.
        """
        return "other"

    dag = DummyDag(user_defined_macros={"other": _other})
    ol_policy.inject_openlineage(make_task(layout, dag=dag))

    assert dag.user_defined_macros["other"] is _other
    assert dag.user_defined_macros[ol_policy.MACRO] is ol_policy.ol_macro


def test_taken_macro_name_blocks_injection(
    layout: SimpleNamespace, jar_ok: list[tuple[str, str]], caplog: pytest.LogCaptureFixture
) -> None:
    """Имя макроса занято чужим объектом: инъекции нет, есть warning."""

    def _foreign() -> str:
        """Чужой объект под нашим именем макроса.

        :return: строка-заглушка.
        """
        return "foreign"

    dag = DummyDag(user_defined_macros={ol_policy.MACRO: _foreign})
    task = make_task(layout, dag=dag)

    ol_policy.inject_openlineage(task)

    assert conf_of(task, layout) is None
    assert dag.user_defined_macros[ol_policy.MACRO] is _foreign
    assert any(ol_policy.MACRO in message for message in warnings_of(caplog))


def test_two_tasks_of_one_dag_are_both_injected(
    layout: SimpleNamespace, jar_ok: list[tuple[str, str]], caplog: pytest.LogCaptureFixture
) -> None:
    """Вторая таска того же DAG'а не считает наш макрос чужим (стендовый spark_etl_dag)."""
    dag = DummyDag()
    first = make_task(layout, dag=dag, task_id="generate")
    second = make_task(layout, dag=dag, task_id="aggregate")

    ol_policy.inject_openlineage(first)
    ol_policy.inject_openlineage(second)

    assert "spark.extraListeners" in conf_of(first, layout)
    assert "spark.extraListeners" in conf_of(second, layout)
    assert warnings_of(caplog) == []


def test_task_without_dag_warns(
    layout: SimpleNamespace, jar_ok: list[tuple[str, str]], caplog: pytest.LogCaptureFixture
) -> None:
    """Макрос положить некуда: warning и отказ от инъекции."""
    task = make_task(layout)
    task.dag = None

    ol_policy.inject_openlineage(task)

    assert conf_of(task, layout) is None
    assert any("DAG" in message for message in warnings_of(caplog))


# ---------------------------------------------------------------------------
# Разбор OPENLINEAGE_JAR (§5.2)
# ---------------------------------------------------------------------------


def test_jar_path_takes_path_only() -> None:
    """Путь берётся из URI, authority игнорируется."""
    assert ol_policy.jar_path(JAR) == "/opt/openlineage/openlineage-spark_2.13-1.46.0.jar"


@pytest.mark.parametrize("value", ["", "   ", "/opt/openlineage/ol.jar", "hdfs://namenode:9000", "ol.jar"])
def test_jar_path_rejects_malformed(value: str) -> None:
    """Значение без схемы или без пути годным не считается."""
    assert ol_policy.jar_path(value) is None


def test_endpoint_host_comes_from_resolver_not_from_jar_uri(
    jar_env: str, endpoints: Callable[[list[str]], None], requests_log: Callable[..., list[str]]
) -> None:
    """Хост эндпоинта берётся из конфигов кластера, а не из authority URI jar'а."""
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
    """Три источника склеиваются в порядке jars → conf → наш, без дубликатов."""
    assert ol_policy.merge_csv("a.jar", "b.jar,a.jar", JAR) == f"a.jar,b.jar,{JAR}"


def test_merge_jars_ignores_non_strings() -> None:
    """``None`` и не-строка дают пустой вклад."""
    assert ol_policy.merge_csv(None, ["b.jar"], JAR) == JAR


@pytest.mark.parametrize(
    "templated",
    ["{{ params.jars | join(', ') }}", "{{ macros.pick('a.jar', 'b.jar') }}", "{% if x %}a.jar{% endif %}"],
)
def test_merge_jars_does_not_split_jinja(templated: str) -> None:
    """Значение с Jinja не режется по запятой: выражение осталось бы битым."""
    assert ol_policy.merge_csv(templated, None, JAR) == f"{templated},{JAR}"
    assert ol_policy.merge_csv(None, templated, JAR) == f"{templated},{JAR}"


def test_dag_jars_survive(layout: SimpleNamespace, jar_ok: list[tuple[str, str]]) -> None:
    """DAG задал ``jars=``: DAG-jar сохраняется внутри вызова макроса."""
    task = make_task(layout, jars="a.jar")

    ol_policy.inject_openlineage(task)

    assert "a.jar" in getattr(task, layout.jars)


def test_conf_jars_are_taken_into_jars_and_left_intact(
    layout: SimpleNamespace, jar_ok: list[tuple[str, str]]
) -> None:
    """DAG задал только ``conf["spark.jars"]``: элементы уезжают в вызов макроса, ключ не тронут."""
    task = make_task(layout, conf={"spark.jars": "b.jar"})

    ol_policy.inject_openlineage(task)

    assert jars_of(task, layout) == ol_policy.parse._macro_call("jar", "none", "b.jar")
    assert conf_of(task, layout)["spark.jars"] == "b.jar"


def test_both_jar_sources_are_merged(layout: SimpleNamespace, jar_ok: list[tuple[str, str]]) -> None:
    """DAG задал и ``jars=``, и ``conf["spark.jars"]``: оба внутри вызова макроса, без дубликатов."""
    task = make_task(layout, jars="a.jar", conf={"spark.jars": "b.jar,a.jar"})

    ol_policy.inject_openlineage(task)

    assert jars_of(task, layout) == ol_policy.parse._macro_call("jar", "none", "a.jar,b.jar")


# ---------------------------------------------------------------------------
# inject_openlineage: сборка строк на парсе, без чтения Variable и HDFS (Task 9)
# ---------------------------------------------------------------------------


def test_inject_writes_macro_calls_not_values(layout: SimpleNamespace) -> None:
    """Парс кладёт в conf вызовы макроса, а не значения из Variable."""
    dag = DummyDag()
    task = layout.cls(dag=dag)

    ol_policy.inject_openlineage(task)

    conf = getattr(task, layout.conf)
    assert conf["spark.openlineage.transport.url"] == "{{ __openlineage_v1('url', none, '') }}"
    assert conf["spark.openlineage.namespace"] == "{{ __openlineage_v1('namespace', none, '') }}"
    assert conf["spark.extraListeners"] == "{{ __openlineage_v1('listener', none, '') }}"
    assert conf["spark.openlineage.transport.type"] == "http"
    assert dag.user_defined_macros[ol_policy.MACRO] is ol_policy.ol_macro


def test_inject_puts_jar_merge_into_the_jars_attribute(layout: SimpleNamespace) -> None:
    """Инвариант 14: итог мерджа jar'ов живёт в атрибуте jars, а не в conf."""
    dag = DummyDag()
    task = layout.cls(dag=dag, jars="a.jar", conf={"spark.jars": "b.jar"})

    ol_policy.inject_openlineage(task)

    assert getattr(task, layout.jars) == "{{ __openlineage_v1('jar', none, 'a.jar,b.jar') }}"
    assert getattr(task, layout.conf)["spark.jars"] == "b.jar"


def test_inject_keeps_dag_listener_as_literal(layout: SimpleNamespace) -> None:
    """DAG-CSV listener'ов уходит литералом — дедуп на рендере возможен."""
    dag = DummyDag()
    task = layout.cls(dag=dag, conf={"spark.extraListeners": "com.example.A"})

    ol_policy.inject_openlineage(task)

    conf = getattr(task, layout.conf)
    assert conf["spark.extraListeners"] == "{{ __openlineage_v1('listener', none, 'com.example.A') }}"


def test_inject_leaves_jinja_dag_value_in_the_string(layout: SimpleNamespace) -> None:
    """DAG-значение со своей Jinja остаётся слева от вызова макроса."""
    dag = DummyDag()
    task = layout.cls(dag=dag, jars="{{ params.jars }}")

    ol_policy.inject_openlineage(task)

    assert getattr(task, layout.jars) == "{{ params.jars }}{{ __openlineage_v1('jar', none, none) }}"


def test_inject_does_not_touch_foreign_conf_keys(layout: SimpleNamespace) -> None:
    """Ключи DAG-conf вне lineage-набора остаются как были."""
    dag = DummyDag()
    task = layout.cls(dag=dag, conf={"spark.app.name": "demo", "spark.executor.cores": "2"})

    ol_policy.inject_openlineage(task)

    conf = getattr(task, layout.conf)
    assert conf["spark.app.name"] == "demo"
    assert conf["spark.executor.cores"] == "2"


def test_inject_never_reads_variable(layout: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    """Инвариант 6: парс не ходит в метастор."""
    models = types.ModuleType("airflow.models")

    class _Forbidden:
        """Дубль Variable, роняющий тест при любом обращении."""

        @staticmethod
        def get(*args: object, **kwargs: object) -> object:
            """Валит тест: на парсе Variable читать нельзя.

            :param args: позиционные аргументы настоящего API.
            :param kwargs: именованные аргументы настоящего API.
            :return: ничего не возвращает.
            """
            pytest.fail("Variable.get вызван на парсе")

    models.Variable = _Forbidden
    package = types.ModuleType("airflow")
    package.models = models
    monkeypatch.setitem(sys.modules, "airflow", package)
    monkeypatch.setitem(sys.modules, "airflow.models", models)

    ol_policy.inject_openlineage(layout.cls(dag=DummyDag()))


def test_inject_never_touches_network(layout: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    """Инвариант 7: парс не делает сетевых вызовов."""

    def _forbidden(*args: object, **kwargs: object) -> object:
        """Валит тест: зонд на парсе запрещён.

        :param args: позиционные аргументы.
        :param kwargs: именованные аргументы.
        :return: ничего не возвращает.
        """
        pytest.fail("сетевой вызов на парсе")

    monkeypatch.setattr(ol_policy.probe, "jar_available", _forbidden)
    monkeypatch.setattr(ol_policy.probe, "urlopen", _forbidden)

    ol_policy.inject_openlineage(layout.cls(dag=DummyDag()))


def test_inject_ignores_openlineage_jar_env(layout: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    """URI jar'а живёт в Variable; переменной окружения политика не знает."""
    monkeypatch.setenv("OPENLINEAGE_JAR", "hdfs://namenode:9000/from-env.jar")
    task = layout.cls(dag=DummyDag())

    ol_policy.inject_openlineage(task)

    assert "from-env.jar" not in getattr(task, layout.jars)


# ---------------------------------------------------------------------------
# Зонд jar в HDFS (§6, §6.1)
# ---------------------------------------------------------------------------


def test_probe_true_on_200(
    endpoints: Callable[[list[str]], None], requests_log: Callable[..., list[str]], caplog: pytest.LogCaptureFixture
) -> None:
    """200 — jar есть, перебор прекращается."""
    endpoints(["http://nn1:9870", "http://nn2:9870"])
    urls = requests_log(lambda url: FakeResponse(200))

    assert ol_policy.jar_available(JAR, "/opt/ol.jar") is True
    assert urls == ["http://nn1:9870/webhdfs/v1/opt/ol.jar?op=GETFILESTATUS"]
    assert warnings_of(caplog) == []


def test_probe_false_on_404_without_warning(
    endpoints: Callable[[list[str]], None], requests_log: Callable[..., list[str]], caplog: pytest.LogCaptureFixture
) -> None:
    """404 — штатное «jar не залит»: False и ни одного warning'а."""
    endpoints(["http://nn1:9870", "http://nn2:9870"])
    urls = requests_log(lambda url: HTTPError(url, 404, "Not Found", {}, None))

    assert ol_policy.jar_available(JAR, "/opt/ol.jar") is False
    assert len(urls) == 1
    assert warnings_of(caplog) == []


def test_probe_moves_to_next_endpoint_on_error(
    endpoints: Callable[[list[str]], None], requests_log: Callable[..., list[str]]
) -> None:
    """Сетевая ошибка — следующий эндпоинт."""
    endpoints(["http://nn1:9870", "http://nn2:9870"])
    urls = requests_log(lambda url: URLError("boom") if "nn1" in url else FakeResponse(200))

    assert ol_policy.jar_available(JAR, "/opt/ol.jar") is True
    assert len(urls) == 2


def test_probe_warns_when_all_endpoints_fail(
    endpoints: Callable[[list[str]], None], requests_log: Callable[..., list[str]], caplog: pytest.LogCaptureFixture
) -> None:
    """Список кончился на сетевых ошибках — False с warning'ом про недоступность."""
    endpoints(["http://nn1:9870", "http://nn2:9870"])
    requests_log(lambda url: URLError("boom"))

    assert ol_policy.jar_available(JAR, "/opt/ol.jar") is False
    assert any("недоступны" in message for message in warnings_of(caplog))


def test_probe_treats_standby_as_next_endpoint(
    endpoints: Callable[[list[str]], None], requests_log: Callable[..., list[str]], caplog: pytest.LogCaptureFixture
) -> None:
    """403 + StandbyException — не отказ, а «спроси активный»."""
    endpoints(["http://nn1:9870", "http://nn2:9870"])
    requests_log(lambda url: standby_error(url) if "nn1" in url else FakeResponse(200))

    assert ol_policy.jar_available(JAR, "/opt/ol.jar") is True
    assert warnings_of(caplog) == []


def test_probe_warns_distinctly_when_all_standby(
    endpoints: Callable[[list[str]], None], requests_log: Callable[..., list[str]], caplog: pytest.LogCaptureFixture
) -> None:
    """Все NameNode в standby — свой текст warning'а, отличимый от недоступности."""
    endpoints(["http://nn1:9870", "http://nn2:9870"])
    requests_log(standby_error)

    assert ol_policy.jar_available(JAR, "/opt/ol.jar") is False
    messages = warnings_of(caplog)
    assert any("standby" in message for message in messages)
    assert not any("недоступны" in message for message in messages)


def test_probe_warns_when_no_endpoints_resolved(
    endpoints: Callable[[list[str]], None], caplog: pytest.LogCaptureFixture
) -> None:
    """Пустой список эндпоинтов — отдельный исход со своим текстом."""
    endpoints([])

    assert ol_policy.jar_available(JAR, "/opt/ol.jar") is False
    assert any("не определены" in message for message in warnings_of(caplog))


def test_probe_warns_when_resolver_raises(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Битый или отсутствующий XML: False и warning про эндпоинты WebHDFS."""

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
    """401 без auth → повтор того же URL с заголовком Authorization: Negotiate."""
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
    """SPNEGO недоступен (нет модуля) → исход error и warning про Kerberos."""
    monkeypatch.setitem(sys.modules, "spnego", None)
    requests_log(lambda url: _http_error(401))
    assert ol_policy.probe._query_endpoint("http://nn1:9870", "/jars/ol.jar") == "error"
    assert any("Kerberos" in message for message in warnings_of(caplog))


def test_probe_401_then_404_is_absent(
    monkeypatch: pytest.MonkeyPatch,
    requests_log: Callable[[Callable[[object], object]], list[object]],
) -> None:
    """Авторизованный повтор получил 404 → jar'а нет (absent, без warning'а)."""
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
    """Первый проход — сплошные ошибки, второй находит jar: итог found, был sleep."""
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


def test_probe_absent_is_terminal_on_first_pass(
    endpoints: Callable[[list[str]], None],
    requests_log: Callable[[Callable[[object], object]], list[object]],
) -> None:
    """404 авторитетен: второго прохода нет."""
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
    """Оба прохода — ошибки: итог down, эндпоинт спрошен дважды, warning один."""
    endpoints(["http://nn1:9870"])
    monkeypatch.setattr(ol_policy.probe, "_sleep", lambda seconds: None)
    urls = requests_log(lambda url: OSError("refused"))
    assert ol_policy.probe._probe("/jars/ol.jar") == "down"
    assert len(urls) == 2
    assert any("недоступны" in message for message in warnings_of(caplog))


def test_error_memo_expires_faster_than_found(
    endpoints: Callable[[list[str]], None],
    requests_log: Callable[[Callable[[object], object]], list[object]],
    monkeypatch: pytest.MonkeyPatch,
    clock: SimpleNamespace,
) -> None:
    """Ошибочный исход мемоизируется на _MEMO_ERROR_TTL_SEC, не на _MEMO_TTL_SEC."""
    endpoints(["http://nn1:9870"])
    monkeypatch.setattr(ol_policy.probe, "_sleep", lambda seconds: None)
    calls = requests_log(lambda url: OSError("refused"))
    assert ol_policy.probe.jar_available("hdfs:///jars/ol.jar", "/jars/ol.jar") is False
    first = len(calls)

    clock.now += ol_policy.probe._MEMO_ERROR_TTL_SEC - 1
    assert ol_policy.probe.jar_available("hdfs:///jars/ol.jar", "/jars/ol.jar") is False
    assert len(calls) == first  # мемо ещё живо

    clock.now += 2
    ol_policy.probe.jar_available("hdfs:///jars/ol.jar", "/jars/ol.jar")
    assert len(calls) > first  # протухло — зонд сходил снова


def test_probe_memoizes_by_jar_uri(
    endpoints: Callable[[list[str]], None], requests_log: Callable[..., list[str]], clock: SimpleNamespace
) -> None:
    """Второй вызов с тем же URI в сеть не ходит."""
    endpoints(["http://nn1:9870"])
    urls = requests_log(lambda url: FakeResponse(200))

    assert ol_policy.jar_available(JAR, "/opt/ol.jar") is True
    assert ol_policy.jar_available(JAR, "/opt/ol.jar") is True
    assert len(urls) == 1


def test_probe_memo_expires(
    endpoints: Callable[[list[str]], None], requests_log: Callable[..., list[str]], clock: SimpleNamespace
) -> None:
    """По истечении TTL отрицательный результат переобнаруживается, а не залипает."""
    endpoints(["http://nn1:9870"])
    urls = requests_log(lambda url: HTTPError(url, 404, "Not Found", {}, None))

    assert ol_policy.jar_available(JAR, "/opt/ol.jar") is False
    clock.now += ol_policy.probe._MEMO_TTL_SEC + 1
    assert ol_policy.jar_available(JAR, "/opt/ol.jar") is False
    assert len(urls) == 2


def test_probe_returns_within_deadline(
    monkeypatch: pytest.MonkeyPatch,
    endpoints: Callable[[list[str]], None],
    requests_log: Callable[..., list[str]],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Вызов возвращается не позже дедлайна, даже если висят все эндпоинты."""
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


def test_late_thread_does_not_overwrite_memo(
    monkeypatch: pytest.MonkeyPatch,
    endpoints: Callable[[list[str]], None],
    requests_log: Callable[..., list[str]],
) -> None:
    """Поток, доехавший после дедлайна, не переписывает опубликованный ``False``."""
    monkeypatch.setattr(ol_policy.probe, "_PROBE_DEADLINE_SEC", 0.1)
    endpoints(["http://nn1:9870"])

    def _slow(url: str) -> object:
        time.sleep(0.4)
        return FakeResponse(200)

    urls = requests_log(_slow)

    assert ol_policy.jar_available(JAR, "/opt/ol.jar") is False
    time.sleep(0.6)
    assert ol_policy.jar_available(JAR, "/opt/ol.jar") is False
    assert len(urls) == 1


def test_probe_thread_is_daemon(monkeypatch: pytest.MonkeyPatch) -> None:
    """Перебор идёт в демон-потоке: пул потоков подвесил бы выход процесса."""
    seen: list[bool] = []

    def _resolve() -> list[str]:
        seen.append(threading.current_thread().daemon)
        return []

    monkeypatch.setattr(ol_policy.probe, "resolve_webhdfs_urls", _resolve)
    ol_policy.jar_available(JAR, "/opt/ol.jar")

    assert seen == [True]


# ---------------------------------------------------------------------------
# Чтение Variable: _cfg (§5.4)
# ---------------------------------------------------------------------------


def test_cfg_returns_dict(variable: Callable[..., SimpleNamespace]) -> None:
    """Валидный объект нового формата разбирается в словарь."""
    variable(raw=json.dumps({
        "enabled": True,
        "spark_conf": {"spark.extraListeners": "io.example.L", "spark.openlineage.transport.url": "http://marquez:5000", "spark.openlineage.namespace": "ns"},
        "openlineage_jar": "hdfs://namenode:9000/opt/ol.jar",
    }))

    assert ol_policy.variable._cfg() == {
        "enabled": True,
        "spark_conf": {"spark.extraListeners": "io.example.L", "spark.openlineage.transport.url": "http://marquez:5000", "spark.openlineage.namespace": "ns"},
        "openlineage_jar": "hdfs://namenode:9000/opt/ol.jar",
    }


def test_cfg_rejects_empty_object(variable: Callable[..., SimpleNamespace], caplog: pytest.LogCaptureFixture) -> None:
    """Пустой JSON-объект — не годная форма Variable."""
    variable(raw="{}")

    assert ol_policy.variable._cfg() is None
    assert any("enabled (bool)" in message for message in warnings_of(caplog))


def test_cfg_rejects_old_shape(variable: Callable[..., SimpleNamespace], caplog: pytest.LogCaptureFixture) -> None:
    """Старый формат Variable ({enabled, url, namespace}) — не валиден."""
    variable(raw='{"enabled": true, "url": "http://marquez:5000", "namespace": "ns"}')

    assert ol_policy.variable._cfg() is None
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
    """Каждая причина отказа даёт ``None`` и свой warning, а не пустой лог."""
    variable(raw=raw, error=error)

    assert ol_policy.variable._cfg() is None
    messages = warnings_of(caplog)
    assert messages
    assert any(marker in message for message in messages)


def test_cfg_is_memoized(variable: Callable[..., SimpleNamespace]) -> None:
    """Повторный вызов в метастор не ходит."""
    state = variable(raw='{"enabled": true}')

    ol_policy.variable._cfg()
    ol_policy.variable._cfg()

    assert state.calls == 1


def test_cfg_warns_about_auth(variable: Callable[..., SimpleNamespace], caplog: pytest.LogCaptureFixture) -> None:
    """Ключ ``auth`` распознаётся, чтобы отказать явно (инвариант 5)."""
    variable(raw=json.dumps({"enabled": True, "spark_conf": {"spark.extraListeners": "io.example.L", "spark.openlineage.transport.url": "http://marquez:5000", "spark.openlineage.namespace": "ns"}, "openlineage_jar": "hdfs://n:9000/o.jar", "auth": {"token": "s3cr3t"}}))

    ol_policy.variable._cfg()

    assert any("auth" in message for message in warnings_of(caplog))
    assert not any("s3cr3t" in message for message in warnings_of(caplog))


def test_validate_cfg_aggregates_missing_fields(
    variable: Callable[..., SimpleNamespace], caplog: pytest.LogCaptureFixture
) -> None:
    """Частичный ``spark_conf`` и пустой ``openlineage_jar`` — один warning с обоими полями."""
    variable(
        raw=json.dumps(
            {
                "enabled": True,
                "spark_conf": {"spark.extraListeners": "io.example.L"},
                "openlineage_jar": "",
            }
        )
    )

    assert ol_policy.ol_macro("listener") == ""

    messages = warnings_of(caplog)
    aggregated = [m for m in messages if "spark.openlineage.transport.url" in m and "openlineage_jar" in m]
    assert aggregated, f"ожидался агрегированный warning, получили: {messages}"


def test_validate_cfg_runs_once_per_ttl(
    variable: Callable[..., SimpleNamespace], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Четыре вызова ol_macro подряд — один проход _validate_cfg (TTL-мемо ещё живо)."""
    state = variable(raw=json.dumps({
        "enabled": True,
        "spark_conf": {
            "spark.extraListeners": "io.example.L",
            "spark.openlineage.transport.url": "http://m:5000",
            "spark.openlineage.namespace": "ns",
        },
        "openlineage_jar": "hdfs://n:9000/o.jar",
    }))
    calls = {"n": 0}
    original = ol_policy.variable._validate_cfg

    def _counted() -> object:
        calls["n"] += 1
        return original()

    monkeypatch.setattr(ol_policy.variable, "_validate_cfg", _counted)
    monkeypatch.setattr(ol_policy.probe, "jar_available", lambda jar_uri, path: True)

    ol_policy.ol_macro("listener")
    ol_policy.ol_macro("url")
    ol_policy.ol_macro("namespace")
    ol_policy.ol_macro("jar")

    assert calls["n"] == 4
    assert state.calls == 1  # Variable.get вызван один раз — мемо не протухло


def test_cfg_memo_expires_by_ttl(
    variable: Callable[..., SimpleNamespace],
    clock: SimpleNamespace,
) -> None:
    """По истечении TTL Variable перечитывается — правка подхватывается."""
    state = variable(raw=SEEDED_VARIABLE)
    assert ol_policy.variable._cfg() is not None
    first = state.calls
    assert ol_policy.variable._cfg() is not None
    assert state.calls == first  # мемо живо

    clock.now += ol_policy.variable._TTL_SEC + 1
    assert ol_policy.variable._cfg() is not None
    assert state.calls == first + 1  # протухло — перечитали


def test_validate_cfg_follows_cfg_ttl(
    variable: Callable[..., SimpleNamespace],
    clock: SimpleNamespace,
) -> None:
    """Валидированный конфиг протухает вместе с сырым."""
    variable(raw=SEEDED_VARIABLE)
    assert ol_policy.variable._validate_cfg() is not None

    variable(raw="{not json")
    clock.now += ol_policy.variable._TTL_SEC + 1
    assert ol_policy.variable._validate_cfg() is None


def test_reset_state_calls_module_resets(monkeypatch: pytest.MonkeyPatch) -> None:
    """Агрегатор зовёт reset() каждого модуля и не лезет в приватные поля."""
    called: list[str] = []
    for name in ("logger", "variable", "probe", "operator"):
        monkeypatch.setattr(getattr(ol_policy, name), "reset", lambda name=name: called.append(name))
    ol_policy.reset_state()
    assert sorted(called) == ["logger", "operator", "probe", "variable"]


# ---------------------------------------------------------------------------
# Макрос ol_macro (§5.1, §5.3, §5.4)
# ---------------------------------------------------------------------------


def test_macro_returns_values(
    variable: Callable[..., SimpleNamespace], jar_ok: list[tuple[str, str]]
) -> None:
    """Полный конфиг: макрос отдаёт listener/url/namespace из spark_conf."""
    variable(
        raw=json.dumps(
            {
                "enabled": True,
                "spark_conf": {
                    "spark.extraListeners": "com.example.OL",
                    "spark.openlineage.transport.url": "http://marquez:5000",
                    "spark.openlineage.namespace": "hadoop-cluster",
                },
                "openlineage_jar": JAR,
            }
        )
    )

    assert ol_policy.ol_macro("listener") == "com.example.OL"
    assert ol_policy.ol_macro("url") == "http://marquez:5000"
    assert ol_policy.ol_macro("namespace") == "hadoop-cluster"


def test_macro_trims_values(
    variable: Callable[..., SimpleNamespace], jar_ok: list[tuple[str, str]]
) -> None:
    """Годные значения попадают в conf обрезанными."""
    variable(
        raw=json.dumps(
            {
                "enabled": True,
                "spark_conf": {
                    "spark.extraListeners": "com.example.OL",
                    "spark.openlineage.transport.url": "  http://marquez:5000  ",
                    "spark.openlineage.namespace": "  ns  ",
                },
                "openlineage_jar": JAR,
            }
        )
    )

    assert ol_policy.ol_macro("url") == "http://marquez:5000"
    assert ol_policy.ol_macro("namespace") == "ns"


def test_macro_is_silent_on_honest_off(
    variable: Callable[..., SimpleNamespace], caplog: pytest.LogCaptureFixture
) -> None:
    """``enabled: false`` — единственный молчаливый отказ."""
    variable(
        raw=json.dumps(
            {
                "enabled": False,
                "spark_conf": {
                    "spark.extraListeners": "com.example.OL",
                    "spark.openlineage.transport.url": "http://marquez:5000",
                    "spark.openlineage.namespace": "ns",
                },
                "openlineage_jar": JAR,
            }
        )
    )

    assert ol_policy.ol_macro("listener") == ""
    assert warnings_of(caplog) == []


def test_macro_ignores_bad_fields_when_honestly_off(
    variable: Callable[..., SimpleNamespace], caplog: pytest.LogCaptureFixture
) -> None:
    """Выключенный конфиг не обязан быть полным: негодные поля не читаются."""
    variable(raw=json.dumps({"enabled": False, "spark_conf": {"spark.openlineage.transport.url": 5000}, "openlineage_jar": JAR}))

    assert ol_policy.ol_macro("url") == ""
    assert warnings_of(caplog) == []


# ---------------------------------------------------------------------------
# Отказ от лайниджа не должен стирать собственное значение DAG'а (ревью Task 9,
# Critical): на литеральном канале DAG-значение обязано пережить любой отказ.
# ---------------------------------------------------------------------------


def test_macro_disabled_keeps_dag_listener(variable: Callable[..., SimpleNamespace]) -> None:
    """``enabled: false``: лайниджа нет, но DAG-listener на литеральном канале не стирается."""
    variable(
        raw=json.dumps(
            {
                "enabled": False,
                "spark_conf": {
                    "spark.extraListeners": "io.ol.L",
                    "spark.openlineage.transport.url": "http://marquez:5000",
                    "spark.openlineage.namespace": "ns",
                },
                "openlineage_jar": JAR,
            }
        )
    )

    assert ol_policy.ol_macro("listener", None, "com.example.A") == "com.example.A"


def test_macro_disabled_keeps_dag_jars(variable: Callable[..., SimpleNamespace]) -> None:
    """``enabled: false``: лайниджа нет, но DAG-jar'ы на литеральном канале не стираются."""
    variable(
        raw=json.dumps(
            {
                "enabled": False,
                "spark_conf": {
                    "spark.extraListeners": "io.ol.L",
                    "spark.openlineage.transport.url": "http://marquez:5000",
                    "spark.openlineage.namespace": "ns",
                },
                "openlineage_jar": JAR,
            }
        )
    )

    assert ol_policy.ol_macro("jar", None, "mylib.jar") == "mylib.jar"


def test_macro_missing_variable_keeps_dag_listener(variable: Callable[..., SimpleNamespace]) -> None:
    """Variable не задана: лайниджа нет, но DAG-listener на литеральном канале не стирается."""
    variable(raw=None)

    assert ol_policy.ol_macro("listener", None, "com.example.A") == "com.example.A"


def test_macro_missing_variable_keeps_dag_jars(variable: Callable[..., SimpleNamespace]) -> None:
    """Variable не задана: лайниджа нет, но DAG-jar'ы на литеральном канале не стираются."""
    variable(raw=None)

    assert ol_policy.ol_macro("jar", None, "mylib.jar") == "mylib.jar"


def test_macro_refusal_keeps_empty_dag_channel_empty(variable: Callable[..., SimpleNamespace]) -> None:
    """Канал '' (DAG молчал): отказ по-прежнему возвращает '', это не регрессия."""
    variable(raw=None)

    assert ol_policy.ol_macro("listener", None, "") == ""


def test_macro_refusal_does_not_double_none_channel(variable: Callable[..., SimpleNamespace]) -> None:
    """Канал None (текст DAG'а уже слева от вызова): отказ возвращает '', а не None-строку."""
    variable(raw=None)

    assert ol_policy.ol_macro("listener", None, None) == ""


def test_macro_unknown_field_keeps_dag_value(
    variable: Callable[..., SimpleNamespace], probe_forbidden: None
) -> None:
    """Инвариант 18: даже незнакомое поле не стирает то, что DAG задал сам."""
    _variable_full(variable)

    assert ol_policy.ol_macro("нет-такого-поля", None, "com.example.A") == "com.example.A"


@pytest.mark.parametrize(
    "raw",
    [
        '{"spark_conf": {"spark.extraListeners": "L", "spark.openlineage.transport.url": "http://m:5000", "spark.openlineage.namespace": "ns"}, "openlineage_jar": "hdfs://x/o.jar"}',
        "{}",
        '{"enabled": "yes"}',
    ],
)
def test_macro_warns_when_enabled_is_not_bool(
    variable: Callable[..., SimpleNamespace], caplog: pytest.LogCaptureFixture, raw: str
) -> None:
    """Отсутствующий или не-булев ``enabled`` — выкл с warning'ом, а не молча."""
    variable(raw=raw)

    assert ol_policy.ol_macro("listener") == ""
    assert any("enabled" in message for message in warnings_of(caplog))


@pytest.mark.parametrize("bad_value", ['""', '"   "', "5000", '"marquez:5000"', "null"])
def test_macro_rejects_bad_url(
    variable: Callable[..., SimpleNamespace], caplog: pytest.LogCaptureFixture, bad_value: str
) -> None:
    """Негодный ``url`` в spark_conf выключает лайнидж и называет поле."""
    variable(
        raw=(
            '{"enabled": true, "spark_conf": '
            '{"spark.extraListeners": "L", '
            f'"spark.openlineage.transport.url": {bad_value}, '
            '"spark.openlineage.namespace": "ns"}, '
            '"openlineage_jar": "hdfs://x/o.jar"}'
        )
    )

    assert ol_policy.ol_macro("listener") == ""
    assert any("url" in message for message in warnings_of(caplog))


@pytest.mark.parametrize("bad_value", ['""', '"   "', "5000", "null"])
def test_macro_rejects_bad_namespace(
    variable: Callable[..., SimpleNamespace], caplog: pytest.LogCaptureFixture, bad_value: str
) -> None:
    """Негодный ``namespace`` в spark_conf выключает лайнидж и называет поле."""
    variable(
        raw=(
            '{"enabled": true, "spark_conf": '
            '{"spark.extraListeners": "L", '
            '"spark.openlineage.transport.url": "http://marquez:5000", '
            f'"spark.openlineage.namespace": {bad_value}' + "}, "
            '"openlineage_jar": "hdfs://x/o.jar"}'
        )
    )

    assert ol_policy.ol_macro("listener") == ""
    assert any("namespace" in message for message in warnings_of(caplog))


def test_force_does_not_bypass_config_validation(
    variable: Callable[..., SimpleNamespace], caplog: pytest.LogCaptureFixture
) -> None:
    """Форс переопределяет только ``enabled``: негодный url всё равно выключает."""
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

    assert ol_policy.ol_macro("listener", True) == ""
    assert any("url" in message for message in warnings_of(caplog))


def test_force_enables_without_enabled_flag(
    variable: Callable[..., SimpleNamespace], jar_ok: list[tuple[str, str]]
) -> None:
    """Форс включает лайнидж при годных url и namespace без ``enabled: true``."""
    variable(
        raw=json.dumps(
            {
                "enabled": False,
                "spark_conf": {
                    "spark.extraListeners": "com.example.OL",
                    "spark.openlineage.transport.url": "http://marquez:5000",
                    "spark.openlineage.namespace": "ns",
                },
                "openlineage_jar": JAR,
            }
        )
    )

    assert ol_policy.ol_macro("listener", True) == "com.example.OL"


def test_macro_never_raises_on_broken_variable(variable: Callable[..., SimpleNamespace]) -> None:
    """Битый JSON не роняет рендер — макрос отдаёт пустую строку."""
    variable(raw="{not json")

    assert ol_policy.ol_macro("url") == ""


def test_config_values_never_reach_the_log(
    variable: Callable[..., SimpleNamespace], caplog: pytest.LogCaptureFixture
) -> None:
    """Инвариант 5: значения url и namespace в лог не пишутся, только имена полей."""
    variable(
        raw=json.dumps(
            {
                "enabled": True,
                "spark_conf": {
                    "spark.extraListeners": "L",
                    "spark.openlineage.transport.url": "http://secret-host:5000",
                    "spark.openlineage.namespace": "  ",
                },
                "openlineage_jar": JAR,
            }
        )
    )

    ol_policy.ol_macro("url")

    joined = "\n".join(warnings_of(caplog))
    assert "secret-host" not in joined


def test_macro_listener_comes_from_variable(
    variable: Callable[..., SimpleNamespace], jar_ok: list[tuple[str, str]]
) -> None:
    """Listener берётся из Variable.spark_conf.spark.extraListeners, не из хардкода."""
    variable(
        raw=json.dumps(
            {
                "enabled": True,
                "spark_conf": {
                    "spark.extraListeners": "com.example.X",
                    "spark.openlineage.transport.url": "http://m:5000",
                    "spark.openlineage.namespace": "ns",
                },
                "openlineage_jar": JAR,
            }
        )
    )

    assert ol_policy.ol_macro("listener") == "com.example.X"


def _variable_full(variable: Callable[..., SimpleNamespace], listener: str = "io.ol.L") -> None:
    """Сидирует годную Variable нового формата.

    :param variable: фикстура подмены Variable.
    :param listener: класс listener'а, который окажется в spark_conf.
    :return: None.
    """
    variable(raw=json.dumps({
        "enabled": True,
        "spark_conf": {
            "spark.extraListeners": listener,
            "spark.openlineage.transport.url": "http://marquez:5000",
            "spark.openlineage.namespace": "hadoop-cluster",
        },
        "openlineage_jar": "hdfs://namenode:9000/o.jar",
    }))


def test_macro_listener_without_dag_value(
    variable: Callable[..., SimpleNamespace], jar_ok: list[tuple[str, str]]
) -> None:
    """Канал '': listener берётся из Variable и возвращается без разделителя."""
    _variable_full(variable)

    assert ol_policy.ol_macro("listener", None, "") == "io.ol.L"


def test_macro_listener_merges_literal_dag_csv(
    variable: Callable[..., SimpleNamespace], jar_ok: list[tuple[str, str]]
) -> None:
    """Канал-литерал: DAG-listener'ы первыми, наш последним."""
    _variable_full(variable)

    merged = ol_policy.ol_macro("listener", None, "com.example.A,com.example.B")

    assert merged == "com.example.A,com.example.B,io.ol.L"


def test_macro_listener_dedups_our_class(
    variable: Callable[..., SimpleNamespace], jar_ok: list[tuple[str, str]]
) -> None:
    """DAG уже назвал наш класс — второй раз он не появляется."""
    _variable_full(variable)

    assert ol_policy.ol_macro("listener", None, "io.ol.L,com.example.A") == "io.ol.L,com.example.A"


def test_macro_listener_prefixes_comma_for_jinja_channel(
    variable: Callable[..., SimpleNamespace], jar_ok: list[tuple[str, str]]
) -> None:
    """Канал None: значение дописывается с ведущей запятой."""
    _variable_full(variable)

    assert ol_policy.ol_macro("listener", None, None) == ",io.ol.L"


def test_macro_url_wins_over_dag_value(
    variable: Callable[..., SimpleNamespace], jar_ok: list[tuple[str, str]], caplog: pytest.LogCaptureFixture
) -> None:
    """OL побеждает по url; DAG-значение попадает только в лог."""
    _variable_full(variable)
    caplog.set_level(logging.INFO)

    assert ol_policy.ol_macro("url", None, "http://dag-marquez:5000") == "http://marquez:5000"
    messages = [record.getMessage() for record in caplog.records]
    assert any("dag-marquez" in message and "marquez:5000" in message for message in messages)


def test_macro_namespace_returns_variable_value(
    variable: Callable[..., SimpleNamespace], jar_ok: list[tuple[str, str]]
) -> None:
    """namespace возвращается скаляром, без разделителей."""
    _variable_full(variable)

    assert ol_policy.ol_macro("namespace", None, "") == "hadoop-cluster"


def test_listener_constant_is_gone() -> None:
    """Инвариант 12: класс listener'а не хардкодится в политике."""
    assert not hasattr(ol_policy, "LISTENER")


# ---------------------------------------------------------------------------
# _resolve_jar: зонд jar'а на рендере
# ---------------------------------------------------------------------------


def test_macro_jar_merges_with_dag_jars(
    variable: Callable[..., SimpleNamespace], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Канал-литерал: DAG-jar'ы первыми, наш последним."""
    _variable_full(variable)
    monkeypatch.setattr(ol_policy.probe, "jar_available", lambda jar_uri, path: True)

    assert ol_policy.ol_macro("jar", None, "a.jar") == "a.jar,hdfs://namenode:9000/o.jar"


def test_macro_jar_alone_when_dag_silent(
    variable: Callable[..., SimpleNamespace], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Канал '': только наш jar, без разделителя."""
    _variable_full(variable)
    monkeypatch.setattr(ol_policy.probe, "jar_available", lambda jar_uri, path: True)

    assert ol_policy.ol_macro("jar", None, "") == "hdfs://namenode:9000/o.jar"


def test_macro_jar_prefixes_comma_for_jinja_channel(
    variable: Callable[..., SimpleNamespace], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Канал None: наш jar дописывается через запятую."""
    _variable_full(variable)
    monkeypatch.setattr(ol_policy.probe, "jar_available", lambda jar_uri, path: True)

    assert ol_policy.ol_macro("jar", None, None) == ",hdfs://namenode:9000/o.jar"


def test_macro_jar_absent_keeps_dag_jars(
    variable: Callable[..., SimpleNamespace], monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Probe не подтвердил jar — наш jar не подмешан, но DAG-jar на литеральном канале выживает."""
    _variable_full(variable)
    monkeypatch.setattr(ol_policy.probe, "jar_available", lambda jar_uri, path: False)

    assert ol_policy.ol_macro("jar", None, "a.jar") == "a.jar"
    assert any("HDFS" in message for message in warnings_of(caplog))


def test_macro_jar_absent_keeps_empty_dag_channel_empty(
    variable: Callable[..., SimpleNamespace], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Probe не подтвердил jar, канал '' (DAG молчал): по-прежнему пустая строка, не регрессия."""
    _variable_full(variable)
    monkeypatch.setattr(ol_policy.probe, "jar_available", lambda jar_uri, path: False)

    assert ol_policy.ol_macro("jar", None, "") == ""


@pytest.mark.parametrize("field", ["listener", "url", "namespace", "jar"])
def test_macro_refuses_every_field_when_jar_is_absent(
    variable: Callable[..., SimpleNamespace], monkeypatch: pytest.MonkeyPatch, field: str
) -> None:
    """Инвариант 19: зонд не подтвердил jar — отказывают все четыре ветки, не только ``jar``.

    Listener без своего класса на classpath роняет драйвер ``ClassNotFoundException``,
    то есть отказ зонда обязан выключать лайнидж целиком, а не превращать «лайниджа
    нет» в «джоба не стартует».
    """
    _variable_full(variable)
    monkeypatch.setattr(ol_policy.probe, "jar_available", lambda jar_uri, path: False)

    assert ol_policy.ol_macro(field, None, "") == ""


@pytest.mark.parametrize(
    ("field", "dag_cur"),
    [
        ("listener", "com.example.A"),
        ("url", "http://dag-marquez:5000"),
        ("namespace", "dag-ns"),
        ("jar", "a.jar"),
    ],
)
def test_macro_jar_absent_keeps_dag_value_of_every_field(
    variable: Callable[..., SimpleNamespace], monkeypatch: pytest.MonkeyPatch, field: str, dag_cur: str
) -> None:
    """Инвариант 18 при отказе зонда: собственное значение DAG'а переживает отказ во всех ветках."""
    _variable_full(variable)
    monkeypatch.setattr(ol_policy.probe, "jar_available", lambda jar_uri, path: False)

    assert ol_policy.ol_macro(field, None, dag_cur) == dag_cur


def test_macro_jar_absent_names_the_reason_once_per_render(
    variable: Callable[..., SimpleNamespace], monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Причину отказа зонда называет только ветка ``jar``: четыре ветки — один warning."""
    _variable_full(variable)
    monkeypatch.setattr(ol_policy.probe, "jar_available", lambda jar_uri, path: False)

    for field in ("jar", "listener", "url", "namespace"):
        ol_policy.ol_macro(field, None, "")

    assert [message for message in warnings_of(caplog) if "HDFS" in message] == [
        "OpenLineage не включён: jar отсутствует или недоступен в HDFS "
        "(hdfs://namenode:9000/o.jar). Залейте его: scripts/seed-openlineage-jar.bat"
    ]


def test_macro_jar_absent_warns_on_every_render(
    variable: Callable[..., SimpleNamespace], monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """§6: причины отказа jar'а не дедуплицируются — иначе воркер молчит следующие 300 с."""
    _variable_full(variable)
    monkeypatch.setattr(ol_policy.probe, "jar_available", lambda jar_uri, path: False)

    ol_policy.ol_macro("jar", None, "")
    ol_policy.ol_macro("jar", None, "")

    assert len([message for message in warnings_of(caplog) if "HDFS" in message]) == 2


def test_macro_jar_malformed_uri_keeps_dag_jars(
    variable: Callable[..., SimpleNamespace], caplog: pytest.LogCaptureFixture
) -> None:
    """``openlineage_jar`` без схемы — наш jar не подмешан, DAG-jar на литеральном канале выживает."""
    variable(raw=json.dumps({
        "enabled": True,
        "spark_conf": {
            "spark.extraListeners": "io.ol.L",
            "spark.openlineage.transport.url": "http://marquez:5000",
            "spark.openlineage.namespace": "ns",
        },
        "openlineage_jar": "/opt/openlineage/o.jar",
    }))

    assert ol_policy.ol_macro("jar", None, "a.jar") == "a.jar"
    assert any("без схемы" in message for message in warnings_of(caplog))


def test_macro_jar_rejects_uri_without_scheme(
    variable: Callable[..., SimpleNamespace], monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """URI без схемы spark-submit трактует как локальный файл — не подмешиваем."""
    variable(raw=json.dumps({
        "enabled": True,
        "spark_conf": {
            "spark.extraListeners": "io.ol.L",
            "spark.openlineage.transport.url": "http://marquez:5000",
            "spark.openlineage.namespace": "ns",
        },
        "openlineage_jar": "/opt/openlineage/o.jar",
    }))

    def _forbidden(jar_uri: str, path: str) -> bool:
        pytest.fail("зонд не должен вызываться для URI без схемы")

    monkeypatch.setattr(ol_policy.probe, "jar_available", _forbidden)

    assert ol_policy.ol_macro("jar", None, "") == ""
    assert any("без схемы" in message for message in warnings_of(caplog))


def test_resolve_jar_probes_with_uri_and_parsed_path(
    variable: Callable[..., SimpleNamespace], monkeypatch: pytest.MonkeyPatch
) -> None:
    """``_resolve_jar`` зовёт ``jar_available`` с исходным URI (ключ мемо) и разобранным путём.

    Все тесты ``test_macro_jar_*`` подменяют ``jar_available`` лямбдой, игнорирующей
    аргументы, поэтому сама передача аргументов не была проверена ни разу.
    """
    variable(
        raw=json.dumps(
            {
                "enabled": True,
                "spark_conf": {
                    "spark.extraListeners": "io.ol.L",
                    "spark.openlineage.transport.url": "http://marquez:5000",
                    "spark.openlineage.namespace": "ns",
                },
                "openlineage_jar": "hdfs://namenode:9000/opt/openlineage/o.jar",
            }
        )
    )
    calls: list[tuple[str, str]] = []

    def _capture(jar_uri: str, path: str) -> bool:
        """Записывает аргументы, с которыми зонд был вызван, и подтверждает jar.

        :param jar_uri: URI jar'а, как он передан вызывающей стороной.
        :param path: путь jar'а внутри HDFS, как он передан вызывающей стороной.
        :return: True.
        """
        calls.append((jar_uri, path))
        return True

    monkeypatch.setattr(ol_policy.probe, "jar_available", _capture)

    ol_policy.ol_macro("jar", None, "")

    assert calls == [("hdfs://namenode:9000/opt/openlineage/o.jar", "/opt/openlineage/o.jar")]


def test_macro_jar_probes_once_per_uri(
    variable: Callable[..., SimpleNamespace], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Два вызова макроса — один поход в сеть: мемо по URI держит результат."""
    _variable_full(variable)
    probed: list[str] = []

    def _counting_probe(path: str) -> str:
        """Считает походы в HDFS и всегда подтверждает jar.

        :param path: путь jar'а внутри HDFS.
        :return: "found".
        """
        probed.append(path)
        return "found"

    monkeypatch.setattr(ol_policy.probe, "_probe", _counting_probe)

    first = ol_policy.ol_macro("jar", None, "")
    second = ol_policy.ol_macro("jar", None, "")

    assert first == second == "hdfs://namenode:9000/o.jar"
    assert probed == ["/o.jar"]


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
    """Не наша таска: тихий return без warning'а."""
    task = NotSparkOperator()
    before = dict(vars(task))

    ol_policy.apply_policy(task)

    assert vars(task) == before
    assert warnings_of(caplog) == []


def test_mapped_task_warns(
    spark_operator: type, jar_ok: list[tuple[str, str]], caplog: pytest.LogCaptureFixture
) -> None:
    """Динамический маппинг не поддерживается — но и не пропускается молча."""

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


def test_spark_task_is_injected_through_apply_policy(
    layout: SimpleNamespace, spark_operator: type, jar_ok: list[tuple[str, str]]
) -> None:
    """Гейт пропускает экземпляр оператора к сборке."""
    task = make_task(layout)

    ol_policy.apply_policy(task)

    assert "spark.extraListeners" in conf_of(task, layout)


def test_policy_survives_missing_provider(layout: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    """Провайдера нет — политике нечего делать, и она об этом не падает."""
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
    """Даже нечитаемые dag_id/task_id не превращают warning в исключение."""

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
    """Непредвиденная ошибка гасится и попадает в лог, а не роняет импорт файла."""

    def _boom(task: object) -> None:
        raise RuntimeError("неожиданно")

    monkeypatch.setattr(ol_policy.parse, "inject_openlineage", _boom)

    ol_policy.apply_policy(make_task(layout))

    assert any("cluster policy" in message for message in warnings_of(caplog))


# ---------------------------------------------------------------------------
# Проброс чужих исключений (инвариант 1, границы)
# ---------------------------------------------------------------------------


def test_passthrough_survives_missing_class(monkeypatch: pytest.MonkeyPatch) -> None:
    """Отсутствие ``AirflowClusterPolicySkipDag`` не обнуляет весь кортеж."""
    created = install_airflow_exceptions(monkeypatch, ("AirflowTaskTimeout", "AirflowClusterPolicyViolation"))

    passthrough = ol_policy.passthrough_exceptions()

    assert set(passthrough) == {created.AirflowTaskTimeout, created.AirflowClusterPolicyViolation}


def test_passthrough_is_empty_without_airflow(monkeypatch: pytest.MonkeyPatch) -> None:
    """Airflow недоступен — кортеж пуст, и это легально."""
    monkeypatch.setitem(sys.modules, "airflow.exceptions", None)

    assert ol_policy.passthrough_exceptions() == ()


@pytest.mark.parametrize("name", ["AirflowTaskTimeout", "AirflowClusterPolicyViolation", "AirflowClusterPolicySkipDag"])
def test_passthrough_exceptions_are_reraised(
    layout: SimpleNamespace, spark_operator: type, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    """Чужой механизм таймаута и чужое решение пропустить DAG политика не гасит."""
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
    """Модуль политики импортируется без Airflow: он не нужен ему на уровне модуля."""
    module = importlib.reload(ol_policy)

    assert module.MACRO == "__openlineage_v1"


# ---------------------------------------------------------------------------
# Инвариант 6: ноль обращений к метастору на парсе
# ---------------------------------------------------------------------------


def test_parse_never_touches_metastore(
    layout: SimpleNamespace,
    spark_operator: type,
    monkeypatch: pytest.MonkeyPatch,
    jar_ok: list[tuple[str, str]],
) -> None:
    """Ни ``Variable.get``, ни ``BaseHook.get_connection`` на этапе парсинга."""

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
# Рендер настоящим Airflow (пропускается там, где его нет)
# ---------------------------------------------------------------------------


def _airflow_installed() -> bool:
    """Установлен ли настоящий Airflow (каталог ``airflow/`` репозитория не в счёт).

    :return: True, если ``airflow.models`` импортируется.
    """
    try:
        importlib.import_module("airflow.models")
    except Exception:
        return False
    return True


@pytest.mark.skipif(not _airflow_installed(), reason="нужен установленный Airflow")
def test_template_renders_in_sandboxed_environment(
    monkeypatch: pytest.MonkeyPatch, jar_ok: list[tuple[str, str]]
) -> None:
    """Инжектируемые шаблоны рендерятся окружением DAG'а без ошибок."""
    from airflow.models import DAG

    listener = "io.openlineage.spark.agent.OpenLineageSparkListener"
    monkeypatch.setattr(
        ol_policy.variable,
        "_cfg",
        lambda: {
            "enabled": True,
            "spark_conf": {
                "spark.extraListeners": listener,
                "spark.openlineage.transport.url": "http://marquez:5000",
                "spark.openlineage.namespace": "hadoop-cluster",
            },
            "openlineage_jar": "hdfs://namenode:9000/o.jar",
        },
    )
    dag = DAG(dag_id="render_probe", schedule=None, start_date=None)
    dag.user_defined_macros = {ol_policy.MACRO: ol_policy.ol_macro}
    env = dag.get_template_env()
    template = {
        "spark.extraListeners": ol_policy.parse._macro_call("listener", "none", ""),
        "spark.openlineage.transport.url": ol_policy.parse._macro_call("url", "none", ""),
        "spark.openlineage.namespace": ol_policy.parse._macro_call("namespace", "none", ""),
    }

    rendered = {key: env.from_string(value).render() for key, value in template.items()}

    assert rendered["spark.extraListeners"] == listener
    assert rendered["spark.openlineage.transport.url"] == "http://marquez:5000"
    assert rendered["spark.openlineage.namespace"] == "hadoop-cluster"


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
    """Значение сидинга — сырая строка JSON: обратный разбор даёт объект."""
    assert isinstance(json.loads(SEEDED_VARIABLE), dict)


def test_seeded_value_is_accepted_by_the_policy(variable: Callable[..., SimpleNamespace]) -> None:
    """Регрессия §9: то, что сидит ``start-airflow.sh``, политика признаёт годным.

    Ровно на этом рассогласовании (сидинг нового формата против кода старого)
    стенд после рестарта попадал в ветку «конфиг негоден — лайниджа нет».
    """
    variable(raw=SEEDED_VARIABLE)

    assert ol_policy.variable._cfg() == json.loads(SEEDED_VARIABLE)
    assert ol_policy.variable._validate_cfg() is not None


def test_double_encoded_value_is_not_an_object() -> None:
    """Регрессия на ``--json``: повторная сериализация даёт строку, а не объект."""
    seeded = json.dumps({"enabled": True, "url": "http://marquez:5000"})
    double_encoded = json.dumps(seeded, indent=2)

    assert not isinstance(json.loads(double_encoded), dict)


# ---------------------------------------------------------------------------
# merge_csv — listener'ы (§8, цикл 1)
# ---------------------------------------------------------------------------


def test_merge_listeners_dag_first_then_our() -> None:
    """DAG-listener первым, OL-listener последним, порядок CSV сохранён."""
    assert ol_policy.merge_csv("com.example.A,com.example.B", "io.ol.L") == "com.example.A,com.example.B,io.ol.L"


def test_merge_listeners_dedups_existing_ol() -> None:
    """Если OL-listener уже в DAG-CSV — дедуп, не дублируется."""
    assert ol_policy.merge_csv("com.example.A,io.ol.L", "io.ol.L") == "com.example.A,io.ol.L"


def test_merge_listeners_only_dag() -> None:
    """Только DAG — возвращаем DAG как есть."""
    assert ol_policy.merge_csv("com.example.A", "") == "com.example.A"


def test_merge_listeners_only_our() -> None:
    """Только OL — возвращаем OL."""
    assert ol_policy.merge_csv("", "io.ol.L") == "io.ol.L"


def test_merge_listeners_both_empty() -> None:
    """Пусто и там, и там — пустая строка."""
    assert ol_policy.merge_csv("", "") == ""


@pytest.mark.parametrize("templated", ["{{ params.listener }}", "{% if x %}A,B{% endif %}"])
def test_merge_listeners_does_not_split_jinja(templated: str) -> None:
    """Jinja-выражение не режется по запятой."""
    assert ol_policy.merge_csv(templated, "io.ol.L") == f"{templated},io.ol.L"
    assert ol_policy.merge_csv("", templated) == templated


def test_emit_without_dag_value_returns_value_as_is(monkeypatch: pytest.MonkeyPatch) -> None:
    """Канал '': DAG молчал — возвращаем значение без разделителя, не заходя в мердж.

    ``utils.merge_csv`` подменён падающим: тест проходит только если пустой канал
    не проваливается в общую ветку, где мердж действительно был бы вызван.
    """

    def _forbidden(*sources: object) -> str:
        """Мердж, которого на пустом канале быть не должно.

        :param sources: CSV-источники, как их принимает настоящий ``merge_csv``.
        :return: не возвращает.
        :raises AssertionError: всегда.
        """
        raise AssertionError("на пустом канале мердж не вызывается")

    monkeypatch.setattr(ol_policy.utils, "merge_csv", _forbidden)

    assert ol_policy.render._emit("io.ol.L", "", "spark.extraListeners") == "io.ol.L"


def test_emit_with_literal_merges_and_dedups() -> None:
    """Канал-литерал: полный мердж с дедупом, DAG-значения первыми."""
    merged = ol_policy.render._emit("io.ol.L", "com.example.A,io.ol.L", "spark.extraListeners")

    assert merged == "com.example.A,io.ol.L"


def test_emit_with_none_channel_prefixes_comma() -> None:
    """Канал None: слева уже стоит текст DAG'а — дописываем через запятую."""
    assert ol_policy.render._emit("io.ol.L", None, "spark.extraListeners") == ",io.ol.L"


def test_emit_jar_branch_splits_and_dedupes() -> None:
    """Ветка jar мержит тем же ``merge_csv`` — сплит и дедуп по тем же правилам."""
    merged = ol_policy.render._emit("hdfs://n:9000/o.jar", "a.jar", "spark.jars")

    assert merged == "a.jar,hdfs://n:9000/o.jar"


# ---------------------------------------------------------------------------
# Сквозные тесты: живой Jinja и различимость причин отказа (Task 10)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _airflow_installed(), reason="нужен установленный Airflow")
def test_full_cycle_renders_expected_command_values(
    variable: Callable[..., SimpleNamespace], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Парс собрал строки, живой Jinja их отрендерил — значения на месте, запятых лишних нет."""
    from airflow.models import dag as airflow_dag

    _variable_full(variable)
    monkeypatch.setattr(ol_policy.probe, "jar_available", lambda jar_uri, path: True)
    dag = airflow_dag.DAG(dag_id="render", schedule=None, start_date=None)
    task = PublicLayoutOperator(dag=dag, jars="a.jar", conf={"spark.extraListeners": "com.example.A"})

    ol_policy.inject_openlineage(task)
    env = dag.get_template_env()
    rendered_conf = {key: env.from_string(value).render() for key, value in task.conf.items()}
    rendered_jars = env.from_string(task.jars).render()

    assert rendered_conf["spark.extraListeners"] == "com.example.A,io.ol.L"
    assert rendered_conf["spark.openlineage.transport.url"] == "http://marquez:5000"
    assert rendered_conf["spark.openlineage.namespace"] == "hadoop-cluster"
    assert rendered_jars == "a.jar,hdfs://namenode:9000/o.jar"


@pytest.mark.skipif(not _airflow_installed(), reason="нужен установленный Airflow")
def test_full_cycle_leaves_no_trailing_comma_when_lineage_is_off(
    variable: Callable[..., SimpleNamespace]
) -> None:
    """Инвариант 17: выключённый лайнидж не оставляет висячей запятой."""
    from airflow.models import dag as airflow_dag

    variable(raw=json.dumps({
        "enabled": False,
        "spark_conf": {
            "spark.extraListeners": "io.ol.L",
            "spark.openlineage.transport.url": "http://marquez:5000",
            "spark.openlineage.namespace": "ns",
        },
        "openlineage_jar": "hdfs://namenode:9000/o.jar",
    }))
    dag = airflow_dag.DAG(dag_id="render_off", schedule=None, start_date=None)
    task = PublicLayoutOperator(dag=dag, jars="a.jar", conf={"spark.extraListeners": "com.example.A"})

    ol_policy.inject_openlineage(task)
    env = dag.get_template_env()

    assert env.from_string(task.jars).render() == "a.jar"
    assert env.from_string(task.conf["spark.extraListeners"]).render() == "com.example.A"


@pytest.mark.skipif(not _airflow_installed(), reason="нужен установленный Airflow")
def test_full_cycle_injects_nothing_when_jar_is_absent(
    variable: Callable[..., SimpleNamespace], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Инвариант 19 сквозным прогоном: зонд отказал — в отрендеренной таске нет ни одного значения OL.

    Ключи ``spark.openlineage.*``, которые парс кладёт литералами, в conf остаются,
    но пустыми: без listener'а и без jar'а они безвредны. Проверяем именно то, что
    роняло драйвер, — класс листенера и URI jar'а.
    """
    from airflow.models import dag as airflow_dag

    _variable_full(variable)
    monkeypatch.setattr(ol_policy.probe, "jar_available", lambda jar_uri, path: False)
    dag = airflow_dag.DAG(dag_id="render_no_jar", schedule=None, start_date=None)
    task = PublicLayoutOperator(dag=dag)

    ol_policy.inject_openlineage(task)
    env = dag.get_template_env()
    rendered_conf = {key: env.from_string(value).render() for key, value in task.conf.items()}
    rendered_jars = env.from_string(task.jars).render()

    assert rendered_conf["spark.extraListeners"] == ""
    assert rendered_conf["spark.openlineage.transport.url"] == ""
    assert rendered_conf["spark.openlineage.namespace"] == ""
    assert rendered_jars == ""
    assert "io.ol.L" not in "".join(rendered_conf.values()) + rendered_jars
    assert "hdfs://namenode:9000/o.jar" not in "".join(rendered_conf.values()) + rendered_jars


@pytest.mark.skipif(not _airflow_installed(), reason="нужен установленный Airflow")
def test_full_cycle_keeps_dag_values_when_jar_is_absent(
    variable: Callable[..., SimpleNamespace], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Инвариант 18 сквозным прогоном: отказ зонда не стирает listener и jar'ы самого DAG'а."""
    from airflow.models import dag as airflow_dag

    _variable_full(variable)
    monkeypatch.setattr(ol_policy.probe, "jar_available", lambda jar_uri, path: False)
    dag = airflow_dag.DAG(dag_id="render_no_jar_dag_values", schedule=None, start_date=None)
    task = PublicLayoutOperator(dag=dag, jars="a.jar", conf={"spark.extraListeners": "com.example.A"})

    ol_policy.inject_openlineage(task)
    env = dag.get_template_env()

    assert env.from_string(task.jars).render() == "a.jar"
    assert env.from_string(task.conf["spark.extraListeners"]).render() == "com.example.A"


def test_failure_reasons_are_pairwise_distinct(
    variable: Callable[..., SimpleNamespace], caplog: pytest.LogCaptureFixture
) -> None:
    """Каждая причина отказа звучит в логе по-своему — иначе расследование слепое."""
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
        ol_policy.ol_macro("listener")
        collected.extend(warnings_of(caplog))

    assert len(collected) == len(set(collected)), f"неразличимые тексты: {collected}"
    assert len(collected) == len(scenarios)


