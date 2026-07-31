from __future__ import annotations

import time

now = time.monotonic


def task_dag(task: object) -> object | None:
    """Получить DAG из таски, если он есть.

    Чтение через ``try``: у настоящего оператора ``dag`` — property, бросающая
    исключение у таски, не привязанной к DAG'у.

    :param task: таска Airflow.
    :return: объект DAG'а либо None.
    """
    try:
        return getattr(task, "dag", None)
    except Exception:
        return None


def dag_and_task_ids(task: object) -> tuple[str, str]:
    """Получить пару ``(dag_id, task_id)`` для сообщений и ключей дедупликации.

    :param task: таска Airflow.
    :return: идентификаторы DAG'а и таски; ``"?"`` там, где их прочитать нельзя.
    """
    try:
        dag = task_dag(task)
        return str(getattr(dag, "dag_id", "?")), str(getattr(task, "task_id", "?"))
    except Exception:
        return "?", "?"


def _jar_items(value: object) -> list[str]:
    """Элементы списка jar'ов из одного источника.

    Значение с Jinja не режется по запятой: атрибут jars и ``conf["spark.jars"]``
    шаблонизируются, и разбиение порвало бы выражение с запятой внутри.

    :param value: значение атрибута jars либо ключа ``spark.jars``.
    :return: список непустых элементов; для не-строки — пустой список.
    """
    if not isinstance(value, str):
        return []
    if "{{" in value or "{%" in value:
        return [value.strip()] if value.strip() else []
    return [item.strip() for item in value.split(",") if item.strip()]

def merge_jars(current: object, conf_jars: object, jar: str) -> str:
    """Склеивает три источника jar'ов: атрибут jars, ``conf["spark.jars"]`` и наш.

    Элементы ``conf["spark.jars"]`` забираются в тот же канал потому, что явный
    ``--jars`` вытесняет ``spark.jars`` как источник значения; сам ключ conf при
    этом не переписывается.

    :param current: текущее значение атрибута jars оператора.
    :param conf_jars: значение ключа ``spark.jars`` из conf таски.
    :param jar: наш jar.
    :return: список jar'ов через запятую, без дубликатов и с сохранением порядка.
    """
    merged: list[str] = []
    for source in (current, conf_jars, jar):
        for item in _jar_items(source):
            if item not in merged:
                merged.append(item)
    return ",".join(merged)

def merge_listeners(dag_cur: object, our_listener: object) -> str:
    """Склеивает CSV-лист listener'ов DAG-уровня с классом из Variable.

    Правила те же, что у ``merge_jars``: пустые элементы отбрасываются,
    значение с Jinja не режется по запятой, порядок сохраняется, дубликаты
    убираются. DAG-listener'ы идут первыми, наш — последним: дедуп защищает
    от двух инстансов одного листенера и, как следствие, от дублирующихся
    событий лайниджа.

    :param dag_cur: значение ``conf["spark.extraListeners"]``, каким его задал DAG.
    :param our_listener: класс listener'а из ``spark_conf["spark.extraListeners"]``.
    :return: список классов через запятую; "" если оба источника пусты.
    """
    merged: list[str] = []
    for source in (dag_cur, our_listener):
        for item in _jar_items(source):
            if item not in merged:
                merged.append(item)
    return ",".join(merged)