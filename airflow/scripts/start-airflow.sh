#!/usr/bin/env bash
# Единый контейнер Airflow: инициализация метаданных, затем scheduler и webserver.
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

# Конфиг OpenLineage живёт в Variable openlineage_config и правится в
# Admin -> Variables без рестарта контейнера. Сидинг идемпотентный: существующую
# переменную не трогаем, иначе правка через UI не пережила бы перезапуск.
# Перезаписать дефолтами можно только явным OPENLINEAGE_CONFIG_RESEED=true.
echo "[init] сидим Variable openlineage_config"
ol_config_json="$(python - <<'PY'
import json

print(json.dumps({
    "enabled": True,
    "spark_conf": {
        "spark.extraListeners": "io.openlineage.spark.agent.OpenLineageSparkListener",
        "spark.openlineage.transport.type": "http",
        "spark.openlineage.transport.url": "http://marquez:5000",
        "spark.openlineage.namespace": "hadoop-cluster",
        "spark.openlineage.columnLineage.datasetLineageEnabled": "true",
    },
    "openlineage_jar": "hdfs://namenode:9000/opt/openlineage/openlineage-spark_2.13-1.46.0.jar",
}))
PY
)"

# БЕЗ --json: этот флаг означает «сериализовать значение», а не «значение уже
# JSON». С ним строка закодировалась бы второй раз, и политика читала бы str
# вместо объекта — лайнидж выключился бы молча.
if [ "${OPENLINEAGE_CONFIG_RESEED:-false}" = "true" ] \
   || ! airflow variables get openlineage_config >/dev/null 2>&1; then
    airflow variables set openlineage_config "${ol_config_json}"
fi

echo "[init] готово, запускаем процессы"

# Креды суперпользователя Postgres и пароль админа UI нужны только для разовой
# инициализации выше; в окружении долгоживущих scheduler/webserver и порождаемых
# ими DAG-задач (виден через os.environ) им делать нечего.
unset AIRFLOW_DB_ADMIN_USER AIRFLOW_DB_ADMIN_PASSWORD AIRFLOW_DB_ADMIN_DB AIRFLOW_ADMIN_PASSWORD

# Схема накатывается до запуска компонентов — документация требует, чтобы во время
# миграции Airflow не работал.
echo "[run] scheduler в фоне"
airflow scheduler &

echo "[run] webserver на переднем плане"
exec airflow webserver
