"""Общие хелперы политики: идентификаторы таски, мердж CSV-значений."""

from __future__ import annotations


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


def _csv_items(value: object) -> list[str]:
    """Элементы CSV-значения одного источника.

    Значение с Jinja не режется по запятой: атрибут ``jars`` и ключи conf
    шаблонизируются, и разбиение порвало бы выражение с запятой внутри.

    :param value: значение атрибута оператора либо ключа conf.
    :return: список непустых элементов; для не-строки — пустой список.
    """
    if not isinstance(value, str):
        return []
    if "{{" in value or "{%" in value:
        return [value.strip()] if value.strip() else []
    return [item.strip() for item in value.split(",") if item.strip()]


def merge_csv(*sources: object) -> str:
    """Склеивает CSV-источники в порядке перечисления, убирая дубликаты.

    Одни правила для jar'ов и для listener'ов. Дедуп listener'ов защищает от двух
    инстансов одного класса и, как следствие, от задвоенных событий лайниджа.

    :param sources: значения источников: атрибут ``jars``, ключи conf, наше значение.
    :return: элементы через запятую; "" если все источники пусты.
    """
    return ",".join(dict.fromkeys(item for source in sources for item in _csv_items(source)))
