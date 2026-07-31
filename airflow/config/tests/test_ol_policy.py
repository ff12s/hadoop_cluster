"""Тесты cluster policy OpenLineage (§9 спеки).

Набор запускается голым ``python -m pytest`` без установленного Airflow: политика
не импортирует его на уровне модуля, а провайдерские классы подменяются дублями.
Каждый тест раскладки атрибутов прогоняется дважды — на приватной раскладке
провайдера 4.1.1 и на публичной раскладке 4.10.0.
"""

from __future__ import annotations

import importlib
import io
import json
import sys
import threading
import time
import types
from types import SimpleNamespace
from typing import Callable
from urllib.error import HTTPError, URLError

import pytest

import ol_policy
from conftest import DummyDag, warnings_of

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

    monkeypatch.setattr(ol_policy, "jar_available", _available)
    return calls


@pytest.fixture
def probe_forbidden(monkeypatch: pytest.MonkeyPatch) -> None:
    """Валит тест, если зонд вообще был вызван.

    :param monkeypatch: фикстура подмены.
    :return: None.
    """

    def _fail(jar_uri: str, path: str) -> bool:
        raise AssertionError("зонд не должен вызываться")

    monkeypatch.setattr(ol_policy, "jar_available", _fail)


@pytest.fixture
def endpoints(monkeypatch: pytest.MonkeyPatch) -> Callable[[list[str]], None]:
    """Подменяет резолвер эндпоинтов WebHDFS заданным списком.

    :param monkeypatch: фикстура подмены.
    :return: функция-настройщик списка эндпоинтов.
    """

    def _set(urls: list[str]) -> None:
        monkeypatch.setattr(ol_policy, "resolve_webhdfs_urls", lambda: list(urls))

    return _set


@pytest.fixture
def requests_log(monkeypatch: pytest.MonkeyPatch) -> Callable[[Callable[[str], object]], list[str]]:
    """Подменяет ``urlopen`` заданным обработчиком и пишет запрошенные URL.

    :param monkeypatch: фикстура подмены.
    :return: функция-настройщик, возвращающая список запрошенных URL.
    """
    urls: list[str] = []

    def _install(handler: Callable[[str], object]) -> list[str]:
        def _urlopen(url: str, timeout: float | None = None) -> object:
            urls.append(url)
            result = handler(url)
            if isinstance(result, BaseException):
                raise result
            return result

        monkeypatch.setattr(ol_policy, "urlopen", _urlopen)
        return urls

    return _install


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """Подменяет модульный источник времени политики управляемым счётчиком.

    :param monkeypatch: фикстура подмены.
    :return: объект с полем ``now``, которое тест двигает вперёд.
    """
    state = SimpleNamespace(now=1000.0)
    monkeypatch.setattr(ol_policy, "_now", lambda: state.now)
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

    assert conf_of(task, layout)["spark.extraListeners"] == f"{{{{ {ol_policy.MACRO}('listener', true) }}}}"
    assert warnings_of(caplog) == []


def test_dag_force_on_is_used_when_task_is_silent(
    layout: SimpleNamespace, jar_ok: list[tuple[str, str]], caplog: pytest.LogCaptureFixture
) -> None:
    """Форс-включение DAG'а действует, если таска не высказалась."""
    task = make_task(layout, dag=DummyDag(params={"openlineage": True}))

    ol_policy.inject_openlineage(task)

    assert conf_of(task, layout)["spark.extraListeners"] == f"{{{{ {ol_policy.MACRO}('listener', true) }}}}"
    assert warnings_of(caplog) == []


def test_missing_toggle_is_neutral_and_silent(
    layout: SimpleNamespace, jar_ok: list[tuple[str, str]], caplog: pytest.LogCaptureFixture
) -> None:
    """Ключа нет ни у таски, ни у DAG'а: решение уходит в Variable, лог пуст."""
    task = make_task(layout)

    ol_policy.inject_openlineage(task)

    assert conf_of(task, layout)["spark.extraListeners"] == f"{{{{ {ol_policy.MACRO}('listener', none) }}}}"
    assert warnings_of(caplog) == []


def test_none_toggle_is_neutral_and_silent(
    layout: SimpleNamespace, jar_ok: list[tuple[str, str]], caplog: pytest.LogCaptureFixture
) -> None:
    """Объявленный нейтральный ``None`` молчит так же, как отсутствие ключа."""
    task = make_task(layout, params={"openlineage": None}, dag=DummyDag(params={"openlineage": None}))

    ol_policy.inject_openlineage(task)

    assert conf_of(task, layout)["spark.extraListeners"] == f"{{{{ {ol_policy.MACRO}('listener', none) }}}}"
    assert warnings_of(caplog) == []


@pytest.mark.parametrize("value", ["yes", 1, [], {}])
def test_non_bool_toggle_warns_and_falls_through(
    layout: SimpleNamespace, jar_ok: list[tuple[str, str]], caplog: pytest.LogCaptureFixture, value: object
) -> None:
    """Негодное значение тумблера игнорируется с warning'ом, решение уходит ниже."""
    task = make_task(layout, params={"openlineage": value})

    ol_policy.inject_openlineage(task)

    assert conf_of(task, layout)["spark.extraListeners"] == f"{{{{ {ol_policy.MACRO}('listener', none) }}}}"
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

    assert conf_of(task, layout)["spark.extraListeners"] == f"{{{{ {ol_policy.MACRO}('listener', none) }}}}"
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


def test_foreign_listener_blocks_injection(
    layout: SimpleNamespace, jar_ok: list[tuple[str, str]], caplog: pytest.LogCaptureFixture
) -> None:
    """Чужой листенер в conf: таска не трогается вовсе, пишется warning."""
    dag = DummyDag()
    task = make_task(layout, dag=dag, conf={"spark.extraListeners": FOREIGN_LISTENER}, jars="a.jar")

    ol_policy.inject_openlineage(task)

    assert conf_of(task, layout) == {"spark.extraListeners": FOREIGN_LISTENER}
    assert jars_of(task, layout) == "a.jar"
    assert dag.user_defined_macros is None
    assert any("DAG" in message for message in warnings_of(caplog))


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


def test_our_own_template_is_not_foreign(
    layout: SimpleNamespace, jar_ok: list[tuple[str, str]], caplog: pytest.LogCaptureFixture
) -> None:
    """Собственный шаблон политики чужим не считается — повторный прогон штатен."""
    task = make_task(layout)

    ol_policy.inject_openlineage(task)
    first = dict(conf_of(task, layout))
    ol_policy.inject_openlineage(task)

    assert conf_of(task, layout) == first
    assert jars_of(task, layout) == JAR
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


@pytest.mark.parametrize("value", ["", "/opt/openlineage/ol.jar", "hdfs://namenode:9000"])
def test_malformed_jar_env_skips_probe(
    layout: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
    probe_forbidden: None,
    caplog: pytest.LogCaptureFixture,
    value: str,
) -> None:
    """Негодная ``OPENLINEAGE_JAR``: warning, отказ и ни одного вызова зонда."""
    monkeypatch.setenv("OPENLINEAGE_JAR", value)
    task = make_task(layout)

    ol_policy.inject_openlineage(task)

    assert conf_of(task, layout) is None
    assert any("OPENLINEAGE_JAR" in message for message in warnings_of(caplog))


def test_unset_jar_env_skips_probe(
    layout: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
    probe_forbidden: None,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Незаданная ``OPENLINEAGE_JAR`` — ветка warning'а, а не ``KeyError``."""
    monkeypatch.delenv("OPENLINEAGE_JAR", raising=False)
    task = make_task(layout)

    ol_policy.inject_openlineage(task)

    assert conf_of(task, layout) is None
    assert any("OPENLINEAGE_JAR" in message for message in warnings_of(caplog))


def test_unavailable_jar_blocks_forced_injection(
    layout: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, jar_env: str, caplog: pytest.LogCaptureFixture
) -> None:
    """Форс не обходит зонд: недоступный jar выключает лайнидж с warning'ом."""
    monkeypatch.setattr(ol_policy, "jar_available", lambda jar_uri, path: False)
    task = make_task(layout, params={"openlineage": True}, dag=DummyDag(dag_id="etl"), task_id="aggregate")

    ol_policy.inject_openlineage(task)

    assert conf_of(task, layout) is None
    messages = warnings_of(caplog)
    assert any("etl" in message and "aggregate" in message for message in messages)


def test_probe_gets_path_from_uri_not_authority(
    layout: SimpleNamespace, jar_ok: list[tuple[str, str]]
) -> None:
    """В зонд уезжают исходный URI (ключ мемо) и разобранный путь."""
    ol_policy.inject_openlineage(make_task(layout))

    assert jar_ok == [(JAR, "/opt/openlineage/openlineage-spark_2.13-1.46.0.jar")]


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
    assert ol_policy.merge_jars("a.jar", "b.jar,a.jar", JAR) == f"a.jar,b.jar,{JAR}"


def test_merge_jars_ignores_non_strings() -> None:
    """``None`` и не-строка дают пустой вклад."""
    assert ol_policy.merge_jars(None, ["b.jar"], JAR) == JAR


@pytest.mark.parametrize(
    "templated",
    ["{{ params.jars | join(', ') }}", "{{ macros.pick('a.jar', 'b.jar') }}", "{% if x %}a.jar{% endif %}"],
)
def test_merge_jars_does_not_split_jinja(templated: str) -> None:
    """Значение с Jinja не режется по запятой: выражение осталось бы битым."""
    assert ol_policy.merge_jars(templated, None, JAR) == f"{templated},{JAR}"
    assert ol_policy.merge_jars(None, templated, JAR) == f"{templated},{JAR}"


def test_dag_jars_survive(layout: SimpleNamespace, jar_ok: list[tuple[str, str]]) -> None:
    """DAG задал ``jars=``: оба jar'а на месте."""
    task = make_task(layout, jars="a.jar")

    ol_policy.inject_openlineage(task)

    assert jars_of(task, layout) == f"a.jar,{JAR}"


def test_conf_jars_are_taken_into_jars_and_left_intact(
    layout: SimpleNamespace, jar_ok: list[tuple[str, str]]
) -> None:
    """DAG задал только ``conf["spark.jars"]``: элементы уезжают в jars, ключ не тронут."""
    task = make_task(layout, conf={"spark.jars": "b.jar"})

    ol_policy.inject_openlineage(task)

    assert jars_of(task, layout) == f"b.jar,{JAR}"
    assert conf_of(task, layout)["spark.jars"] == "b.jar"


def test_both_jar_sources_are_merged(layout: SimpleNamespace, jar_ok: list[tuple[str, str]]) -> None:
    """DAG задал и ``jars=``, и ``conf["spark.jars"]``: в jars все три, без дубликатов."""
    task = make_task(layout, jars="a.jar", conf={"spark.jars": "b.jar,a.jar"})

    ol_policy.inject_openlineage(task)

    assert jars_of(task, layout) == f"a.jar,b.jar,{JAR}"


def test_dag_conf_wins(layout: SimpleNamespace, jar_ok: list[tuple[str, str]]) -> None:
    """Явный conf DAG'а побеждает OL-ключи."""
    task = make_task(layout, conf={"spark.openlineage.namespace": "custom"})

    ol_policy.inject_openlineage(task)

    assert conf_of(task, layout)["spark.openlineage.namespace"] == "custom"
    assert conf_of(task, layout)["spark.openlineage.transport.type"] == "http"


def test_injected_keys_are_exactly_five(layout: SimpleNamespace, jar_ok: list[tuple[str, str]]) -> None:
    """Инжектируются ровно пять ключей §5.4, три из них — вызовы макроса."""
    task = make_task(layout)

    ol_policy.inject_openlineage(task)

    assert set(conf_of(task, layout)) == {
        "spark.extraListeners",
        "spark.openlineage.transport.type",
        "spark.openlineage.transport.url",
        "spark.openlineage.namespace",
        "spark.openlineage.columnLineage.datasetLineageEnabled",
    }


# ---------------------------------------------------------------------------
# Порядок мутаций (инвариант 3)
# ---------------------------------------------------------------------------


def test_no_mutation_when_assembly_fails(
    layout: SimpleNamespace,
    spark_operator: type,
    monkeypatch: pytest.MonkeyPatch,
    jar_ok: list[tuple[str, str]],
) -> None:
    """Исключение до первой мутации не оставляет следов ни в conf, ни в jars."""

    def _boom(forced_on: bool) -> dict[str, str]:
        raise RuntimeError("сборка сломалась")

    monkeypatch.setattr(ol_policy, "ol_conf_template", _boom)
    dag = DummyDag()
    task = make_task(layout, dag=dag, conf={"spark.app.name": "demo"}, jars="a.jar")

    ol_policy.apply_policy(task)

    assert conf_of(task, layout) == {"spark.app.name": "demo"}
    assert jars_of(task, layout) == "a.jar"
    assert dag.user_defined_macros is None


def test_interrupted_mutation_leaves_jar_without_listener(
    layout: SimpleNamespace, spark_operator: type, jar_ok: list[tuple[str, str]]
) -> None:
    """Обрыв на последней мутации оставляет jar без листенера, а не наоборот."""

    class Blocked(layout.cls):
        """Дубль, у которого запись conf падает."""

        def __setattr__(self, name: str, value: object) -> None:
            """Блокирует запись атрибута conf после инициализации.

            :param name: имя атрибута.
            :param value: значение.
            :return: None.
            :raises RuntimeError: при записи conf у готового объекта.
            """
            if name == layout.conf and getattr(self, "ready", False):
                raise RuntimeError("обрыв на записи conf")
            object.__setattr__(self, name, value)

    dag = DummyDag()
    task = Blocked(dag=dag, conf={"spark.app.name": "demo"})
    task.ready = True

    ol_policy.apply_policy(task)

    assert conf_of(task, layout) == {"spark.app.name": "demo"}
    assert jars_of(task, layout) == JAR
    assert dag.user_defined_macros[ol_policy.MACRO] is ol_policy.ol_macro


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

    monkeypatch.setattr(ol_policy, "resolve_webhdfs_urls", _raise)

    assert ol_policy.jar_available(JAR, "/opt/ol.jar") is False
    assert any("не удалось определить эндпоинты" in message for message in warnings_of(caplog))


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
    clock.now += ol_policy._MEMO_TTL_SEC + 1
    assert ol_policy.jar_available(JAR, "/opt/ol.jar") is False
    assert len(urls) == 2


def test_many_tasks_cause_one_network_trip(
    layout: SimpleNamespace,
    jar_env: str,
    endpoints: Callable[[list[str]], None],
    requests_log: Callable[..., list[str]],
) -> None:
    """N тасок одного файла при недостижимом NameNode дают один поход в сеть."""
    endpoints(["http://nn1:9870"])
    urls = requests_log(lambda url: URLError("boom"))
    dag = DummyDag()

    for index in range(3):
        ol_policy.inject_openlineage(make_task(layout, dag=dag, task_id=f"t{index}"))

    assert len(urls) == 1


def test_probe_returns_within_deadline(
    monkeypatch: pytest.MonkeyPatch,
    endpoints: Callable[[list[str]], None],
    requests_log: Callable[..., list[str]],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Вызов возвращается не позже дедлайна, даже если висят все эндпоинты."""
    monkeypatch.setattr(ol_policy, "_PROBE_DEADLINE_SEC", 0.2)
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
    monkeypatch.setattr(ol_policy, "_PROBE_DEADLINE_SEC", 0.1)
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

    monkeypatch.setattr(ol_policy, "resolve_webhdfs_urls", _resolve)
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

    assert ol_policy._cfg() == {
        "enabled": True,
        "spark_conf": {"spark.extraListeners": "io.example.L", "spark.openlineage.transport.url": "http://marquez:5000", "spark.openlineage.namespace": "ns"},
        "openlineage_jar": "hdfs://namenode:9000/opt/ol.jar",
    }


def test_cfg_rejects_old_shape(variable: Callable[..., SimpleNamespace], caplog: pytest.LogCaptureFixture) -> None:
    """Старый формат Variable ({enabled, url, namespace}) — не валиден."""
    variable(raw='{"enabled": true, "url": "http://marquez:5000", "namespace": "ns"}')

    assert ol_policy._cfg() is None
    messages = warnings_of(caplog)
    assert any("spark_conf" in message for message in messages)
    assert any("openlineage_jar" in message for message in messages)


def test_cfg_returns_empty_dict_for_empty_object(
    variable: Callable[..., SimpleNamespace], caplog: pytest.LogCaptureFixture
) -> None:
    """Пустой объект — легальное значение, а не сентинел отказа."""
    variable(raw="{}")

    assert ol_policy._cfg() is None
    assert any("spark_conf" in m for m in warnings_of(caplog))


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

    assert ol_policy._cfg() is None
    messages = warnings_of(caplog)
    assert messages
    assert any(marker in message for message in messages)


def test_cfg_is_memoized(variable: Callable[..., SimpleNamespace]) -> None:
    """Повторный вызов в метастор не ходит."""
    state = variable(raw='{"enabled": true}')

    ol_policy._cfg()
    ol_policy._cfg()

    assert state.calls == 1


def test_cfg_warns_about_auth(variable: Callable[..., SimpleNamespace], caplog: pytest.LogCaptureFixture) -> None:
    """Ключ ``auth`` распознаётся, чтобы отказать явно (инвариант 5)."""
    variable(raw=json.dumps({"enabled": True, "spark_conf": {"spark.extraListeners": "io.example.L", "spark.openlineage.transport.url": "http://marquez:5000", "spark.openlineage.namespace": "ns"}, "openlineage_jar": "hdfs://n:9000/o.jar", "auth": {"token": "s3cr3t"}}))

    ol_policy._cfg()

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


def test_validate_cfg_runs_once_per_process(
    variable: Callable[..., SimpleNamespace], monkeypatch: pytest.MonkeyPatch
) -> None:
    """_validate_cfg кэшируется на уровне процесса (lru_cache)."""
    variable(
        raw=json.dumps(
            {
                "enabled": True,
                "spark_conf": {
                    "spark.extraListeners": "io.example.L",
                    "spark.openlineage.transport.url": "http://marquez:5000",
                    "spark.openlineage.namespace": "ns",
                },
                "openlineage_jar": JAR,
            }
        )
    )

    ol_policy._validate_cfg.cache_clear()
    ol_policy.ol_macro("listener")
    ol_policy.ol_macro("url")
    ol_policy.ol_macro("namespace")
    ol_policy.ol_macro("jar")

    assert ol_policy._validate_cfg.cache_info().hits == 3
    assert ol_policy._validate_cfg.cache_info().misses == 1


# ---------------------------------------------------------------------------
# Макрос ol_macro (§5.1, §5.3, §5.4)
# ---------------------------------------------------------------------------


def test_macro_returns_values(variable: Callable[..., SimpleNamespace]) -> None:
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


def test_macro_trims_values(variable: Callable[..., SimpleNamespace]) -> None:
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


def test_force_enables_without_enabled_flag(variable: Callable[..., SimpleNamespace]) -> None:
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


def test_macro_listener_comes_from_variable(variable: Callable[..., SimpleNamespace]) -> None:
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


def test_listener_constant_absent() -> None:
    """Инвариант 12: класс листенера больше не хардкодится константой модуля."""
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
    monkeypatch.setattr(ol_policy, "_spark_submit_operator", lambda: layout.cls)
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
    monkeypatch.setattr(ol_policy, "_spark_submit_operator", lambda: None)

    ol_policy.apply_policy(make_task(layout))


@pytest.mark.parametrize(
    ("jar", "params", "drop_dag"),
    [
        ("мусор", None, False),
        (None, None, False),
        (JAR, {"openlineage": "yes"}, False),
        (JAR, RaisingParams({"openlineage": True}), False),
        (JAR, None, True),
        (JAR, None, False),
    ],
)
def test_policy_never_raises(
    layout: SimpleNamespace,
    spark_operator: type,
    monkeypatch: pytest.MonkeyPatch,
    jar: str | None,
    params: object,
    drop_dag: bool,
) -> None:
    """Инвариант 1: политика не бросает и не мутирует таску на любом мусоре.

    ``HADOOP_CONF_DIR`` указывает в никуда, поэтому последний случай доходит до
    зонда и падает там же, где падал бы не смонтированный конфиг кластера.
    """
    monkeypatch.setenv("HADOOP_CONF_DIR", "/nonexistent")
    if jar is None:
        monkeypatch.delenv("OPENLINEAGE_JAR", raising=False)
    else:
        monkeypatch.setenv("OPENLINEAGE_JAR", jar)
    task = make_task(layout, params=params, conf={"spark.app.name": "demo"})
    if drop_dag:
        task.dag = None

    ol_policy.apply_policy(task)

    assert conf_of(task, layout) == {"spark.app.name": "demo"}
    assert jars_of(task, layout) is None


def test_policy_never_raises_on_unreachable_namenode(
    layout: SimpleNamespace, spark_operator: type, monkeypatch: pytest.MonkeyPatch, jar_env: str
) -> None:
    """Недостижимый NameNode деградирует в «лайниджа нет», а не в исключение."""
    monkeypatch.setattr(ol_policy, "resolve_webhdfs_urls", lambda: ["http://nowhere.invalid:9870"])
    monkeypatch.setattr(ol_policy, "urlopen", lambda url, timeout=None: (_ for _ in ()).throw(URLError("down")))
    task = make_task(layout)

    ol_policy.apply_policy(task)

    assert conf_of(task, layout) is None


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

    monkeypatch.setattr(ol_policy, "inject_openlineage", _boom)

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

    monkeypatch.setattr(ol_policy, "inject_openlineage", _boom)

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
def test_template_renders_in_sandboxed_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Инжектируемые шаблоны рендерятся окружением DAG'а без ошибок."""
    from airflow.models import DAG

    monkeypatch.setattr(
        ol_policy,
        "_cfg",
        lambda: {"enabled": True, "url": "http://marquez:5000", "namespace": "hadoop-cluster"},
    )
    dag = DAG(dag_id="render_probe", schedule=None, start_date=None)
    dag.user_defined_macros = {ol_policy.MACRO: ol_policy.ol_macro}
    env = dag.get_template_env()
    template = ol_policy.ol_conf_template(False)

    rendered = {key: env.from_string(value).render() for key, value in template.items()}

    assert rendered["spark.extraListeners"] == ol_policy.LISTENER
    assert rendered["spark.openlineage.transport.url"] == "http://marquez:5000"
    assert rendered["spark.openlineage.namespace"] == "hadoop-cluster"


# ---------------------------------------------------------------------------
# Сидинг Variable (§7)
# ---------------------------------------------------------------------------


def test_seeded_value_parses_back_to_dict() -> None:
    """Значение сидинга — сырая строка JSON: обратный разбор даёт объект."""
    seeded = json.dumps({"enabled": True, "url": "http://marquez:5000", "namespace": "hadoop-cluster"})

    assert isinstance(json.loads(seeded), dict)


def test_double_encoded_value_is_not_an_object() -> None:
    """Регрессия на ``--json``: повторная сериализация даёт строку, а не объект."""
    seeded = json.dumps({"enabled": True, "url": "http://marquez:5000"})
    double_encoded = json.dumps(seeded, indent=2)

    assert not isinstance(json.loads(double_encoded), dict)


# ---------------------------------------------------------------------------
# merge_listeners (§8, цикл 1)
# ---------------------------------------------------------------------------


def test_merge_listeners_dag_first_then_our() -> None:
    """DAG-listener первым, OL-listener последним, порядок CSV сохранён."""
    assert ol_policy.merge_listeners("com.example.A,com.example.B", "io.ol.L") == "com.example.A,com.example.B,io.ol.L"


def test_merge_listeners_dedups_existing_ol() -> None:
    """Если OL-listener уже в DAG-CSV — дедуп, не дублируется."""
    assert ol_policy.merge_listeners("com.example.A,io.ol.L", "io.ol.L") == "com.example.A,io.ol.L"


def test_merge_listeners_only_dag() -> None:
    """Только DAG — возвращаем DAG как есть."""
    assert ol_policy.merge_listeners("com.example.A", "") == "com.example.A"


def test_merge_listeners_only_our() -> None:
    """Только OL — возвращаем OL."""
    assert ol_policy.merge_listeners("", "io.ol.L") == "io.ol.L"


def test_merge_listeners_both_empty() -> None:
    """Пусто и там, и там — пустая строка."""
    assert ol_policy.merge_listeners("", "") == ""


@pytest.mark.parametrize("templated", ["{{ params.listener }}", "{% if x %}A,B{% endif %}"])
def test_merge_listeners_does_not_split_jinja(templated: str) -> None:
    """Jinja-выражение не режется по запятой."""
    assert ol_policy.merge_listeners(templated, "io.ol.L") == f"{templated},io.ol.L"
    assert ol_policy.merge_listeners("", templated) == templated


