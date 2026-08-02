"""Живая проверка namespace-резолвера: multi-host JDBC namespace и Marquez.

Негативный контроль в этом файле необратимо отравляет Marquez: он отправляет
namespace с запятой, из-за которой Marquez создаёт строку в таблице
``namespaces`` под сырым именем и после этого ``GET /api/v1/namespaces``
падает с 500 до конца жизни инстанса (см. README, раздел "Живые e2e-тесты").
Поэтому негативный контроль обязан идти последним, а позитивный — первым.
Порядок внутри файла зависит от порядка определения функций, а порядок между
файлами — от имени файла: ``test_ol_policy_e2e.py`` < ``test_resolver_e2e.py``
по алфавиту, так что pytest сначала соберёт и прогонит тесты политики и только
затем — этот файл. Не переименовывать обратно в
``test_namespace_resolver_e2e.py`` — это уронит файл раньше ``test_ol_policy_e2e.py``.
"""

from __future__ import annotations

import datetime as dt

from conftest import (
    marquez_get,
    read_variable,
    trigger_dag,
    wait_for_run,
    write_variable,
)

DAG_ID = "spark_jdbc_lineage_dag"
RESOLVER_CONF_KEY = "spark.openlineage.dataset.namespaceResolvers.default.type"
RESOLVER_JAR_MARKER = "openlineage-namespace-resolver"
BROKEN_NAMESPACE_MARK = ","
FIXED_NAMESPACE_MARK = "+"


def _dataset_namespaces_since(since: dt.datetime) -> set[str]:
    """Собирает namespace'ы всех датасетов из событий лайниджа после момента времени.

    :param since: момент, начиная с которого читаются события.
    :return: множество namespace'ов входных и выходных датасетов.
    """
    response = marquez_get(
        "/api/v1/events/lineage",
        {"after": since.strftime("%Y-%m-%dT%H:%M:%SZ"), "limit": "500"},
    )
    assert response.status_code == 200, f"GET /events/lineage -> {response.status_code}"
    found: set[str] = set()
    for event in response.json()["events"]:
        for side in ("inputs", "outputs"):
            for dataset in event.get(side) or []:
                found.add(dataset["namespace"])
    return found


def test_with_resolver_multihost_namespace_is_normalized() -> None:
    """С jar'ом резолвера namespace нормализуется и датасет доезжает до Marquez.

    :return: None.
    """
    config = read_variable()
    assert config["spark_conf"][RESOLVER_CONF_KEY] == "normalize", "конфиг резолвера не восстановлен"
    assert RESOLVER_JAR_MARKER in str(config["openlineage_jar"]), "jar резолвера не восстановлен"

    started = dt.datetime.utcnow() - dt.timedelta(seconds=5)
    run_id = trigger_dag(DAG_ID)
    assert wait_for_run(DAG_ID, run_id) == "success"

    namespaces = _dataset_namespaces_since(started)
    postgres_namespaces = {item for item in namespaces if item.startswith("postgres://")}
    assert postgres_namespaces, f"датасет PostgreSQL не появился ни в одном событии: {namespaces}"
    assert all(FIXED_NAMESPACE_MARK in item for item in postgres_namespaces), (
        f"namespace не нормализован: {postgres_namespaces}"
    )
    assert all(BROKEN_NAMESPACE_MARK not in item for item in postgres_namespaces)

    assert marquez_get("/api/v1/namespaces").status_code == 200, (
        "GET /namespaces упал — в список namespace'ов попал невалидный элемент"
    )


def test_without_resolver_multihost_namespace_poisons_marquez() -> None:
    """Без jar'а резолвера Marquez принимает namespace с запятой и необратимо ломается.

    Измерено на живом стенде: запятая в namespace доезжает до ``POST /api/v1/lineage``
    в неизменном виде (событие содержит сырой ``postgres://postgres:5432,marquez-db:5432``),
    и Marquez не отвергает его — отвечает 201. Санирование (замена запятой на ``_``)
    происходит только при записи в таблицу ``datasets`` и в самом событии не видно;
    поэтому ``GET /api/v1/events/lineage`` в окне негативного контроля должен вернуть
    namespace с сырой запятой, а не его санированную форму. Параллельно с этим Marquez
    заводит в таблице ``namespaces`` строку под тем же сырым именем с запятой, и после
    этого ``GET /api/v1/namespaces`` навсегда отвечает 500. Это последний тест набора:
    после него ``GET /api/v1/namespaces`` недоступен, а в ``finally`` восстанавливается
    только Airflow Variable, но не сам Marquez.

    :return: None.
    """
    original = read_variable()
    spark_conf = dict(original["spark_conf"])
    spark_conf.pop(RESOLVER_CONF_KEY, None)
    jars = ",".join(
        uri for uri in str(original["openlineage_jar"]).split(",") if RESOLVER_JAR_MARKER not in uri
    )
    write_variable({**original, "spark_conf": spark_conf, "openlineage_jar": jars})
    try:
        started = dt.datetime.utcnow() - dt.timedelta(seconds=5)
        run_id = trigger_dag(DAG_ID)
        state = wait_for_run(DAG_ID, run_id)
        assert state == "success", f"сама Spark-джоба обязана отработать, а получили состояние {state!r}"

        namespaces = _dataset_namespaces_since(started)
        postgres_namespaces = {item for item in namespaces if item.startswith("postgres://")}
        broken_namespaces = {item for item in postgres_namespaces if BROKEN_NAMESPACE_MARK in item}
        assert broken_namespaces, (
            "в событиях лайниджа нет postgres-namespace с сырой запятой — резолвер, похоже, "
            f"остался активным для этого прогона: {postgres_namespaces}"
        )
        resolver_shaped = {item for item in postgres_namespaces if FIXED_NAMESPACE_MARK in item}
        assert not resolver_shaped, (
            "namespace содержит форму резолвера '+' — значит резолвер не был отключён для этого прогона: "
            f"{resolver_shaped}"
        )

        response = marquez_get("/api/v1/namespaces")
        assert response.status_code == 500, (
            f"GET /namespaces вернул {response.status_code}, ожидалось 500 — отравление namespace "
            "с запятой не произошло, негативный контроль не сработал"
        )
    finally:
        write_variable(original)
