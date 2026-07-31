"""Инфраструктура набора тестов cluster policy OpenLineage.

Кладёт каталог ``airflow/config`` в ``sys.path``: набор обязан запускаться голым
``python -m pytest airflow/config/tests`` без ``PYTHONPATH`` и без установленного
Airflow. Модульное состояние политики (дедупликация warning'ов, мемо конфига и
зонда) сбрасывается до и после каждого теста — оно переживает границу теста,
а ``caplog`` нет.
"""

from __future__ import annotations

import logging
import pathlib
import sys
import types
from types import SimpleNamespace
from typing import Callable, Iterator

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import ol_policy  # noqa: E402  импорт возможен только после правки sys.path


class PrivateLayoutOperator:
    """Дубль ``SparkSubmitOperator`` провайдера 4.1.1: conf и jars приватные."""

    template_fields = ("_application", "_conf", "_jars")

    def __init__(
        self,
        task_id: str = "submit",
        dag: object = None,
        conf: dict[str, object] | None = None,
        jars: str | None = None,
        params: object = None,
    ) -> None:
        """Создаёт дубль таски с приватной раскладкой атрибутов.

        :param task_id: идентификатор таски.
        :param dag: объект DAG'а либо None.
        :param conf: начальный conf таски.
        :param jars: начальное значение атрибута jars.
        :param params: params таски.
        :return: None.
        """
        self.task_id = task_id
        self.dag = dag
        self._application = "job.py"
        self._conf = conf
        self._jars = jars
        self.params = {} if params is None else params
        self.on_execute_callback: object | None = None


class PublicLayoutOperator:
    """Дубль ``SparkSubmitOperator`` провайдера 4.10.0: conf и jars публичные."""

    template_fields = ("application", "conf", "jars")

    def __init__(
        self,
        task_id: str = "submit",
        dag: object = None,
        conf: dict[str, object] | None = None,
        jars: str | None = None,
        params: object = None,
    ) -> None:
        """Создаёт дубль таски с публичной раскладкой атрибутов.

        :param task_id: идентификатор таски.
        :param dag: объект DAG'а либо None.
        :param conf: начальный conf таски.
        :param jars: начальное значение атрибута jars.
        :param params: params таски.
        :return: None.
        """
        self.task_id = task_id
        self.dag = dag
        self.application = "job.py"
        self.conf = conf
        self.jars = jars
        self.params = {} if params is None else params
        self.on_execute_callback: object | None = None


class DummyDag:
    """Дубль DAG'а: только то, что читает политика."""

    def __init__(
        self,
        dag_id: str = "dag",
        params: object = None,
        user_defined_macros: dict[str, object] | None = None,
    ) -> None:
        """Создаёт дубль DAG'а.

        :param dag_id: идентификатор DAG'а.
        :param params: params DAG'а.
        :param user_defined_macros: словарь макросов либо None.
        :return: None.
        """
        self.dag_id = dag_id
        self.params = {} if params is None else params
        self.user_defined_macros = user_defined_macros


_LAYOUTS = {
    "private": SimpleNamespace(name="private", cls=PrivateLayoutOperator, conf="_conf", jars="_jars"),
    "public": SimpleNamespace(name="public", cls=PublicLayoutOperator, conf="conf", jars="jars"),
}


@pytest.fixture(params=["private", "public"])
def layout(request: pytest.FixtureRequest) -> SimpleNamespace:
    """Раскладка атрибутов оператора: приватная (4.1.1) и публичная (4.10.0).

    :param request: запрос pytest с именем раскладки.
    :return: пространство имён с классом дубля и именами атрибутов conf/jars.
    """
    return _LAYOUTS[request.param]


@pytest.fixture(autouse=True)
def _reset_policy_state() -> Iterator[None]:
    """Приводит модульное состояние политики к чистому до и после каждого теста.

    :return: None.
    """
    ol_policy.reset_state()
    yield
    ol_policy.reset_state()


@pytest.fixture(autouse=True)
def _capture_warnings(caplog: pytest.LogCaptureFixture) -> None:
    """Включает захват warning'ов политики во всех тестах набора.

    :param caplog: фикстура захвата лога.
    :return: None.
    """
    caplog.set_level(logging.WARNING)


@pytest.fixture
def variable(monkeypatch: pytest.MonkeyPatch) -> Callable[..., SimpleNamespace]:
    """Подставляет дубль ``airflow.models.Variable`` вместо настоящего Airflow.

    :param monkeypatch: фикстура подмены.
    :return: функция-настройщик ``(raw=..., error=...)``, возвращающая состояние дубля.
    """
    state = SimpleNamespace(raw=None, error=None, calls=0)

    class _Variable:
        """Дубль ``airflow.models.Variable`` со счётчиком обращений."""

        @staticmethod
        def get(key: str, default_var: object = None, **kwargs: object) -> object:
            """Возвращает заранее заданное сырое значение переменной.

            :param key: имя переменной.
            :param default_var: значение по умолчанию.
            :param kwargs: прочие аргументы настоящего API.
            :return: сырое значение либо ``default_var``.
            """
            state.calls += 1
            if state.error is not None:
                raise state.error
            return state.raw if state.raw is not None else default_var

    models = types.ModuleType("airflow.models")
    models.Variable = _Variable
    package = types.ModuleType("airflow")
    package.models = models
    monkeypatch.setitem(sys.modules, "airflow", package)
    monkeypatch.setitem(sys.modules, "airflow.models", models)

    def _configure(raw: object = None, error: BaseException | None = None) -> SimpleNamespace:
        """Задаёт, что вернёт дубль ``Variable.get``.

        :param raw: сырое значение переменной.
        :param error: исключение, которое поднимет ``get``.
        :return: состояние дубля.
        """
        state.raw = raw
        state.error = error
        return state

    return _configure


def warnings_of(caplog: pytest.LogCaptureFixture) -> list[str]:
    """Тексты записей уровня WARNING и выше, уже с подставленными аргументами.

    :param caplog: фикстура захвата лога.
    :return: список отформатированных сообщений.
    """
    return [record.getMessage() for record in caplog.records if record.levelno >= logging.WARNING]
