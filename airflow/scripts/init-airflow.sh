#!/usr/bin/env bash
# Одноразовая инициализация Airflow: база метаданных, схема, учётка администратора.
set -euo pipefail

echo "[init] создаём роль и базу метаданных"
python /opt/airflow/scripts/ensure_db.py

echo "[init] накатываем схему (в 2.6.x команда называется db init, не migrate)"
airflow db init

# users create идемпотентна: на существующем пользователе печатает "already exist
# in the db" и завершается нулём, пароль при этом не меняет.
# Пароль подаётся в stdin (без --password): argv процесса виден всему контейнеру.
echo "[init] создаём пользователя ${AIRFLOW_ADMIN_USER:-admin}"
admin_password="${AIRFLOW_ADMIN_PASSWORD:-admin}"
printf '%s\n%s\n' "${admin_password}" "${admin_password}" | airflow users create \
    --username "${AIRFLOW_ADMIN_USER:-admin}" \
    --firstname Air \
    --lastname Flow \
    --role Admin \
    --email admin@example.com

echo "[init] готово"
