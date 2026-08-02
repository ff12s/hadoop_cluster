"""Инфраструктура живых E2E-тестов стенда.

Тесты гоняются хостовым интерпретатором против уже поднятого стенда: DAG'и
запускаются через ``docker exec`` в контейнере Airflow, результат читается из
REST API Marquez. Весь набор скипается, если стенд недоступен.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
import uuid
from typing import Iterator

import pytest
import requests

AIRFLOW_CONTAINER = "hadoop-airflow"
MARQUEZ_BASE_URL = "http://localhost:5000"
OL_NAMESPACE = "hadoop-cluster"
OL_VARIABLE = "openlineage_config"
RUN_TIMEOUT_SEC = 900
POLL_INTERVAL_SEC = 10
LIVE_E2E_OPT_IN_VAR = "OL_LIVE_E2E"


def docker_exec(args: list[str], container: str = AIRFLOW_CONTAINER) -> str:
    """Выполняет команду в контейнере и возвращает её stdout.

    :param args: аргументы команды без ``docker exec <container>``.
    :param container: имя контейнера.
    :return: stdout команды.
    :raises AssertionError: команда завершилась ненулевым кодом.
    """
    completed = subprocess.run(
        ["docker", "exec", container, *args],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise AssertionError(f"{' '.join(args)} -> {completed.returncode}\n{completed.stderr}")
    return completed.stdout


def airflow_cli(args: list[str]) -> str:
    """Выполняет команду Airflow CLI в контейнере.

    :param args: аргументы после слова ``airflow``.
    :return: stdout команды.
    """
    return docker_exec(["airflow", *args])


def read_variable(name: str = OL_VARIABLE) -> dict[str, object]:
    """Читает Airflow Variable и разбирает её как JSON-объект.

    :param name: имя переменной.
    :return: разобранный объект.
    """
    parsed: dict[str, object] = json.loads(airflow_cli(["variables", "get", name]))
    return parsed


def write_variable(payload: dict[str, object], name: str = OL_VARIABLE) -> None:
    """Записывает Airflow Variable значением сериализованного объекта.

    :param payload: объект конфига.
    :param name: имя переменной.
    :return: None.
    """
    airflow_cli(["variables", "set", name, json.dumps(payload)])


def trigger_dag(dag_id: str) -> str:
    """Запускает DAG и возвращает идентификатор запуска.

    :param dag_id: идентификатор DAG'а.
    :return: сгенерированный ``run_id``.
    """
    run_id = f"live_{uuid.uuid4().hex[:12]}"
    airflow_cli(["dags", "trigger", "-r", run_id, dag_id])
    return run_id


def wait_for_run(dag_id: str, run_id: str, timeout: int = RUN_TIMEOUT_SEC) -> str:
    """Ждёт финального состояния запуска DAG'а.

    :param dag_id: идентификатор DAG'а.
    :param run_id: идентификатор запуска.
    :param timeout: предельное время ожидания в секундах.
    :return: финальное состояние: ``success`` либо ``failed``.
    :raises AssertionError: запуск не дошёл до финального состояния за отведённое время.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        rows = json.loads(airflow_cli(["dags", "list-runs", "-d", dag_id, "-o", "json"]))
        for row in rows:
            if row.get("run_id") == run_id and row.get("state") in {"success", "failed"}:
                state: str = row["state"]
                return state
        time.sleep(POLL_INTERVAL_SEC)
    raise AssertionError(f"запуск {dag_id}/{run_id} не завершился за {timeout} с")


def marquez_get(path: str, params: dict[str, str] | None = None) -> requests.Response:
    """Выполняет GET к API Marquez.

    :param path: путь начиная с ``/api/v1``.
    :param params: параметры строки запроса.
    :return: ответ requests.
    """
    return requests.get(f"{MARQUEZ_BASE_URL}{path}", params=params, timeout=30)


@pytest.fixture(scope="session", autouse=True)
def stand_is_up() -> None:
    """Скипает набор, если стенд не поднят, и валит сессию, если Marquez отравлен.

    Готовность Marquez проверяется через ``GET /api/v1/events/lineage``, а не
    ``GET /api/v1/namespaces`` — последний навсегда отвечает 500 после того, как
    негативный контроль в ``test_resolver_e2e.py`` отравил namespace с запятой, и
    его нельзя использовать как индикатор "стенд не поднят". Отравление — это не
    недоступность: если ``/namespaces`` всё же вернул 500 при живом стенде, сессия
    падает явной ошибкой, а не скипается, чтобы прогон не выглядел прошедшим.

    :return: None.
    :raises Exception: сессия принудительно останавливается через ``pytest.exit``,
        если Marquez поднят, но его namespace отравлен прошлым прогоном.
    """
    if os.environ.get(LIVE_E2E_OPT_IN_VAR, "").lower() != "true":
        pytest.skip(
            f"живой набор пропущен: задайте переменную окружения {LIVE_E2E_OPT_IN_VAR}=true, "
            "чтобы явно подтвердить прогон против стенда (последний тест набора необратимо "
            "отравляет Marquez)",
            allow_module_level=True,
        )
    try:
        response = marquez_get("/api/v1/events/lineage", {"limit": "1"})
    except requests.RequestException as error:
        pytest.skip(f"Marquez недоступен: {error}")
    if response.status_code != 200:
        pytest.skip(f"Marquez ответил {response.status_code}")
    names = subprocess.run(
        ["docker", "ps", "--filter", f"name=^{AIRFLOW_CONTAINER}$", "--format", "{{.Names}}"],
        capture_output=True,
        text=True,
        check=False,
    )
    if AIRFLOW_CONTAINER not in names.stdout:
        pytest.skip(f"контейнер {AIRFLOW_CONTAINER} не запущен")

    poisoned = marquez_get("/api/v1/namespaces")
    if poisoned.status_code == 500:
        pytest.exit(
            "Marquez поднят, но GET /api/v1/namespaces отвечает 500 — namespace отравлен "
            "прошлым прогоном негативного контроля (запятая в multi-host JDBC namespace). "
            "Результаты этого прогона были бы бессмысленны: Marquez не умеет удалять такой "
            "namespace через свой API. Сбросьте стенд перед следующим прогоном: "
            "docker compose down -v, затем поднимите стенд заново.",
            returncode=1,
        )


@pytest.fixture
def restore_variable() -> Iterator[None]:
    """Возвращает Variable ``openlineage_config`` к исходному значению после теста.

    :return: None.
    """
    original = read_variable()
    yield
    write_variable(original)
