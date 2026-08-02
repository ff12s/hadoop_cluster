"""Общие хелперы политики: идентификаторы таски, мердж CSV-значений."""

from __future__ import annotations


def task_dag(task: object) -> object | None:
    """Возвращает DAG таски, не бросая на таске без DAG'а.

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
    """Разбирает CSV-значение одного источника в список элементов.

    Значение с Jinja возвращается целиком: разбиение по запятой порвало бы выражение.

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

    :param sources: значения источников: атрибут ``jars``, ключи conf, наше значение.
    :return: элементы через запятую; "" если все источники пусты.
    """
    return ",".join(dict.fromkeys(item for source in sources for item in _csv_items(source)))
