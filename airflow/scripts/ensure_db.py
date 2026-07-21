"""Идемпотентное создание роли и базы метаданных Airflow в общем Postgres стенда.

Скрипт вызывается из init-airflow.sh до `airflow db init`. Он работает и на
свежем volume, и на уже существующем — в отличие от docker-entrypoint-initdb.d,
который отрабатывает только при первичной инициализации кластера Postgres.
"""

from __future__ import annotations

import os
import sys

import psycopg2
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT


def ensure_role_and_database(
    host: str,
    port: int,
    admin_user: str,
    admin_password: str,
    admin_db: str,
    role: str,
    role_password: str,
    database: str,
) -> None:
    """Создаёт роль и базу, если их ещё нет.

    :param host: хост Postgres.
    :param port: порт Postgres.
    :param admin_user: суперпользователь, от имени которого выполняются DDL.
    :param admin_password: пароль суперпользователя.
    :param admin_db: база для служебного подключения.
    :param role: имя создаваемой роли.
    :param role_password: пароль создаваемой роли.
    :param database: имя создаваемой базы.
    :return: None
    """
    conn = psycopg2.connect(
        host=host, port=port, user=admin_user, password=admin_password, dbname=admin_db
    )
    conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,))
            if cur.fetchone() is None:
                cur.execute(f'CREATE ROLE "{role}" LOGIN PASSWORD %s', (role_password,))
                print(f"роль {role} создана")
            else:
                print(f"роль {role} уже существует")

            cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (database,))
            if cur.fetchone() is None:
                cur.execute(f'CREATE DATABASE "{database}" OWNER "{role}"')
                print(f"база {database} создана")
            else:
                print(f"база {database} уже существует")
    finally:
        conn.close()


def main() -> int:
    """Читает параметры подключения из окружения и создаёт роль с базой.

    :return: код возврата процесса.
    """
    ensure_role_and_database(
        host=os.environ.get("AIRFLOW_DB_HOST", "postgres"),
        port=int(os.environ.get("AIRFLOW_DB_PORT", "5432")),
        admin_user=os.environ.get("AIRFLOW_DB_ADMIN_USER", "hive"),
        admin_password=os.environ.get("AIRFLOW_DB_ADMIN_PASSWORD", "hive"),
        admin_db=os.environ.get("AIRFLOW_DB_ADMIN_DB", "hive_metastore"),
        role=os.environ.get("AIRFLOW_DB_USER", "airflow"),
        role_password=os.environ.get("AIRFLOW_DB_PASSWORD", "airflow"),
        database=os.environ.get("AIRFLOW_DB_NAME", "airflow"),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
