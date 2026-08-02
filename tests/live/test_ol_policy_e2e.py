"""Живая проверка: cluster policy доводит OpenLineage до Spark-джобы Airflow.

Два теста в этом файле намеренно зависят от порядка: второй переиспользует
результат прогона DAG'а, сделанного первым. Прогон Spark-джобы на YARN стоит
минуты, дублировать его ради независимости тестов не стоит — так решено
осознанно, не трогать порядок при рефакторинге. Зависимость сделана явной
через module-scoped фикстуру ``dag_run``, а не неявно через общее состояние
стенда: второй тест не сможет случайно "пройти" на лайнидже, оставленном
каким-то более ранним прогоном, потому что фикстура сама поднимает прогон и
даёт временную границу для проверки его следов.
"""

from __future__ import annotations

import datetime as dt
from typing import NamedTuple

import pytest
from conftest import marquez_get, trigger_dag, wait_for_run

DAG_ID = "spark_etl_dag"
EXPECTED_JOB_SUBSTRINGS = ("airflow_etl_generate", "airflow_etl_aggregate")
OL_NAMESPACE = "hadoop-cluster"
AGG_DATASET_NAMESPACE = "hdfs://namenode:9000"
AGG_DATASET_NAME = "/user/hadoop/airflow_demo/agg.parquet"
AGG_DATASET_NODE_ID = f"dataset:{AGG_DATASET_NAMESPACE}:{AGG_DATASET_NAME}"


class DagRun(NamedTuple):
    """Единственный прогон ``spark_etl_dag``, общий для обоих тестов файла."""

    run_id: str
    started: dt.datetime
    state: str


@pytest.fixture(scope="module")
def dag_run() -> DagRun:
    """Запускает ``spark_etl_dag`` один раз на модуль и отдаёт его результат обоим тестам.

    :return: идентификатор прогона, момент запуска (с запасом в 5 с на рассинхрон часов) и
        финальное состояние.
    """
    started = dt.datetime.utcnow() - dt.timedelta(seconds=5)
    run_id = trigger_dag(DAG_ID)
    state = wait_for_run(DAG_ID, run_id)
    return DagRun(run_id=run_id, started=started, state=state)


def test_spark_etl_dag_emits_lineage_to_marquez(dag_run: DagRun) -> None:
    """Прогон spark_etl_dag завершается успехом и оставляет джобы в Marquez.

    :param dag_run: результат единственного прогона DAG'а на модуль.
    :return: None.
    """
    assert dag_run.state == "success"

    namespaces = marquez_get("/api/v1/namespaces")
    assert namespaces.status_code == 200
    assert OL_NAMESPACE in {item["name"] for item in namespaces.json()["namespaces"]}

    jobs = marquez_get(f"/api/v1/namespaces/{OL_NAMESPACE}/jobs", {"limit": "200"})
    assert jobs.status_code == 200
    names = " ".join(item["name"] for item in jobs.json()["jobs"])
    for expected in EXPECTED_JOB_SUBSTRINGS:
        assert expected in names, f"джоба {expected} не появилась в Marquez: {names}"


def test_spark_etl_dag_lineage_graph_has_edges(dag_run: DagRun) -> None:
    """Граф лайниджа выходного датасета содержит ребро, оставленное именно прогоном ``dag_run``.

    Проверяем со стороны датасета, а не джобы. OpenLineage заводит на каждый
    Spark action родительскую джобу (``airflow_etl_generate``) и дочернюю
    (``unknown.airflow_etl_generate...``); рёбра ввода-вывода висят на дочерней,
    а поиск "первой джобы с подходящим именем" в списке из Marquez детерминированно
    выбирает родительскую — у неё нет рёбер, и проверка ложно валилась. Датасет
    один и не зависит от того, какая из джоб его записала.

    Одного непустого ``inEdges`` недостаточно: на стенде, где `spark_etl_dag` уже
    гонялся раньше, этот граф остаётся ненулевым и без текущего прогона. Поэтому
    вдобавок проверяем событие лайниджа с этим датасетом на выходе в окне времени
    ``dag_run`` — доказательство того, что ребро принадлежит именно этому прогону,
    а не более раннему.

    :param dag_run: результат единственного прогона DAG'а на модуль.
    :return: None.
    """
    assert dag_run.state == "success"

    graph = marquez_get("/api/v1/lineage", {"nodeId": AGG_DATASET_NODE_ID, "depth": "5"})
    assert graph.status_code == 200, f"GET /lineage -> {graph.status_code} для {AGG_DATASET_NODE_ID}"

    nodes = graph.json()["graph"]
    matching = [node for node in nodes if node["id"] == AGG_DATASET_NODE_ID]
    assert matching, f"датасет {AGG_DATASET_NODE_ID} отсутствует в графе: {[node['id'] for node in nodes]}"

    in_edges = matching[0]["inEdges"]
    assert in_edges, f"у датасета {AGG_DATASET_NODE_ID} нет входящих рёбер — ничего его не произвело: {matching[0]}"

    events = marquez_get(
        "/api/v1/events/lineage",
        {"after": dag_run.started.strftime("%Y-%m-%dT%H:%M:%SZ"), "limit": "500"},
    )
    assert events.status_code == 200, f"GET /events/lineage -> {events.status_code}"
    produced_in_window = any(
        dataset["namespace"] == AGG_DATASET_NAMESPACE and dataset["name"] == AGG_DATASET_NAME
        for event in events.json()["events"]
        for dataset in event.get("outputs") or []
    )
    assert produced_in_window, (
        f"датасет {AGG_DATASET_NODE_ID} не встретился как output ни в одном событии лайниджа "
        f"после {dag_run.started.isoformat()} — рёбра в графе принадлежат более раннему прогону"
    )
