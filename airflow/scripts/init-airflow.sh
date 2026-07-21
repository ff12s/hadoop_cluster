#!/usr/bin/env bash
# Одноразовая инициализация Airflow: база метаданных, схема, учётка администратора.
set -euo pipefail

echo "[init] создаём роль и базу метаданных"
python /opt/airflow/scripts/ensure_db.py

echo "[init] накатываем схему (в 2.6.x команда называется db init, не migrate)"
airflow db init

ADMIN_USER="${AIRFLOW_ADMIN_USER:-admin}"
if airflow users list --output plain | awk 'NR>1 {print $2}' | grep -Fxq "${ADMIN_USER}"; then
    echo "[init] пользователь ${ADMIN_USER} уже существует"
else
    echo "[init] создаём пользователя ${ADMIN_USER}"
    airflow users create \
        --username "${ADMIN_USER}" \
        --password "${AIRFLOW_ADMIN_PASSWORD:-admin}" \
        --firstname Air \
        --lastname Flow \
        --role Admin \
        --email admin@example.com
fi

echo "[init] готово"
