"""Идемпотентное создание роли и базы метаданных Airflow в общем Postgres стенда.

Скрипт вызывается из start-airflow.sh до `airflow db init`. Он работает и на
свежем volume, и на уже существующем — в отличие от docker-entrypoint-initdb.d,
который отрабатывает только при первичной инициализации кластера Postgres.
"""

from __future__ import annotations

import os
import sys
import time

import psycopg2
from psycopg2 import sql
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT, connection

CONNECT_ATTEMPTS = 60
CONNECT_DELAY_SEC = 2.0


def connect_with_retry(
    host: str,
    port: int,
    user: str,
    password: str,
    dbname: str,
) -> connection:
    """Подключается к Postgres, повторяя попытки до готовности сервера.

    На свежем volume postgres:13 несколько секунд выполняет initdb и слушает
    только unix-сокет, отвергая TCP-подключения.

    :param host: хост Postgres.
    :param port: порт Postgres.
    :param user: пользователь подключения.
    :param password: пароль пользователя.
    :param dbname: база для служебного подключения.
    :return: открытое соединение.
    :raises psycopg2.OperationalError: если сервер не поднялся за отведённое время.
    """
    attempt = 0
    while True:
        attempt += 1
        try:
            return psycopg2.connect(host=host, port=port, user=user, password=password, dbname=dbname)
        except psycopg2.OperationalError:
            # на последней попытке ошибка уходит наверх и роняет init
            if attempt >= CONNECT_ATTEMPTS:
                raise
            print(f"Postgres недоступен, попытка {attempt}/{CONNECT_ATTEMPTS}")
            time.sleep(CONNECT_DELAY_SEC)


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
    """Создаёт роль и базу, если их ещё нет, и приводит пароль роли к переданному.

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
    conn = connect_with_retry(host=host, port=port, user=admin_user, password=admin_password, dbname=admin_db)
    conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,))
            if cur.fetchone() is None:
                # sql.Identifier квотирует и экранирует имя; f-строка этого не делает
                cur.execute(
                    sql.SQL("CREATE ROLE {} LOGIN PASSWORD %s").format(sql.Identifier(role)),
                    (role_password,),
                )
                print(f"роль {role} создана")
            else:
                # пароль роли мог измениться в .env — иначе строка подключения Airflow разойдётся с ролью
                cur.execute(
                    sql.SQL("ALTER ROLE {} WITH LOGIN PASSWORD %s").format(sql.Identifier(role)),
                    (role_password,),
                )
                print(f"роль {role} уже существует, пароль синхронизирован")

            cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (database,))
            if cur.fetchone() is None:
                cur.execute(
                    sql.SQL("CREATE DATABASE {} OWNER {}").format(
                        sql.Identifier(database), sql.Identifier(role)
                    )
                )
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
