# Контейнер Airflow 2.6.3 в тест-стенде hadoop_cluster

**Дата:** 2026-07-21
**Статус:** утверждён (brainstorming), готов к плану реализации
**Артефакт:** сервисы `airflow-init` / `airflow-webserver` / `airflow-scheduler` + образ `hadoop-cluster-airflow`
**Репозиторий:** `hadoop_cluster` (изменения только здесь)

## 1. Контекст и цель

Тест-стенд `hadoop_cluster` поднимает Hadoop 3.3.6 / YARN, Hive 3.1.3, Spark 3.5.2, Kyuubi, JupyterLab и
Marquez 0.47.0 одним `docker-compose.yml`. Spark-джобы уже отправляются на YARN вручную (`tests/test-spark.bat`,
ноутбуки) и шлют lineage в Marquez штатным OpenLineage-листенером из `spark/config/spark-defaults.conf`.

Не хватает оркестратора: нет способа проверить связку «Airflow → spark-submit → YARN → Marquez», которая
воспроизводит продовый путь запуска. Цель — добавить в стенд Airflow с версией, параметризованной через
`.env` (по умолчанию **2.6.3**), и пару демонстрационных DAG'ов, запускающих Spark на кластере.

**Не входит в скоуп:** Airflow-уровневый OpenLineage (`openlineage-airflow`), Kerberos, CeleryExecutor,
несколько воркеров, продовая конфигурация (secrets backend, RBAC-роли, Fernet-ключ из хранилища).

## 2. Грундинг-бриф — обязателен во всех брифах реализации

Запинён на **Airflow 2.6.3**, провайдер выбирается constraints-файлом этой же версии.

| Факт | Источник |
| --- | --- |
| Airflow 2.6.3 поддерживает Python 3.7–3.11, **не 3.12** → образ стенда `hadoop-cluster-spark` (PYTHON_VERSION=3.12.7) базой быть не может | pypi.org/project/apache-airflow/2.6.3/ (релиз 2023-07-10) |
| Default-тег `apache/airflow:<version>` = «новейший поддерживаемый Python на момент релиза» — неоднозначно → пинуем `-python3.10` | airflow.apache.org/docs/docker-stack/index.html |
| `constraints-2.6.3/constraints-3.10.txt` пинит `apache-airflow-providers-apache-spark==4.1.1`, `pyspark==3.4.1` | raw.githubusercontent.com/apache/airflow/constraints-2.6.3/constraints-3.10.txt (verbatim) |
| **`SparkSubmitOperator` 4.1.1 не имеет параметров `deploy_mode`, `properties_file`, `use_krb5ccache`, `post_submit_commands`** — они появились в новых провайдерах. Передача → `TypeError` | provider docs 4.1.1, `operators/spark_submit` (дельта: context7 `/websites/airflow_apache` отдаёт docs *stable*-провайдера, здесь запинен 4.1.1) |
| Полная сигнатура 4.1.1: `application, conf, conn_id, files, py_files, archives, driver_class_path, jars, java_class, packages, exclude_packages, repositories, total_executor_cores, executor_cores, executor_memory, driver_memory, keytab, principal, proxy_user, name, num_executors, status_poll_interval, application_args, env_vars, verbose, spark_binary` | там же |
| Deploy-mode в 4.1.1 задаётся **только** extra коннекшена `{"deploy-mode": "cluster"}` | hook 4.1.1, `_resolve_connection` |
| Master строится как `f"{conn.host}:{conn.port}"` при наличии порта, иначе `conn.host`; при отсутствии коннекшена — дефолт `"yarn"`. `_is_yarn = "yarn" in master` | hook 4.1.1 |
| `spark_binary` допускает только `spark-submit`/`spark2-submit`/`spark3-submit`; extra `spark-home` **бросает исключение**; бинарь обязан быть в `PATH` | hook 4.1.1 |
| YARN application id парсится из лога **только** при `_is_yarn and deploy_mode == "cluster"`; `on_kill` зовёт внешний бинарь `yarn application -kill` | hook 4.1.1 |
| `submit()` бросает `AirflowException` при ненулевом коде возврата spark-submit | hook 4.1.1 |
| **В 2.6.3 нет команды `airflow db migrate`** (появилась в 2.7). Подкоманды `airflow db`: `check, check-migrations, clean, downgrade, drop-archived, export-archived, init, reset, shell, upgrade` → используем `airflow db init` (идемпотентна, накатывает миграции) | airflow.apache.org/docs/apache-airflow/2.6.3/cli-and-env-variables-ref.html |
| Официальный compose 2.6.3: `airflow-init` инициализирует БД + создаёт админа; `AIRFLOW__CORE__EXECUTOR`, `AIRFLOW__DATABASE__SQL_ALCHEMY_CONN`, `AIRFLOW__CORE__LOAD_EXAMPLES`, `AIRFLOW__CORE__FERNET_KEY`, `AIRFLOW__SCHEDULER__ENABLE_HEALTH_CHECK`; healthcheck `curl --fail http://localhost:8080/health` (webserver) и `:8974/health` (scheduler); volume'ы `./dags ./logs ./config ./plugins`; `AIRFLOW_UID` по умолчанию 50000 | airflow.apache.org/docs/apache-airflow/2.6.3/docker-compose.yaml |
| `_PIP_ADDITIONAL_REQUIREMENTS` ставит пакеты при **каждом** старте контейнера — официально не для продакшена, вместо него собственный образ | там же |
| Redis/Celery/worker/flower нужны только для CeleryExecutor | там же |

## 3. Образ `airflow/Dockerfile`

Мультистейдж, забирающий готовые дистрибутивы из уже собранных образов стенда — ничего не скачивается повторно:

```dockerfile
FROM hadoop-cluster-spark:latest AS sparkdist        # Spark 3.5.2 + OpenLineage jar внутри
FROM hadoop-cluster-base:latest  AS hadoopdist       # Hadoop 3.3.6 (нужен бинарь yarn)

FROM apache/airflow:${AIRFLOW_VERSION}-python3.10
USER root
#  openjdk-11-jre-headless: в debian-базе образа Airflow нет openjdk-8,
#  Spark 3.5 официально поддерживает Java 8/11/17
COPY --from=sparkdist  /opt/spark          /opt/spark
COPY --from=hadoopdist /opt/hadoop         /opt/hadoop
COPY --from=sparkdist  /opt/scripts/pyspark_pi.py /opt/airflow/jobs/pyspark_pi.py
COPY jobs/ /opt/airflow/jobs/
USER airflow
RUN pip install --no-cache-dir apache-airflow-providers-apache-spark \
      --constraint "https://raw.githubusercontent.com/apache/airflow/constraints-${AIRFLOW_VERSION}/constraints-3.10.txt"
```

Версия провайдера **не хардкодится**: её выбирает constraints-файл соответствующей версии Airflow (для 2.6.3 —
`4.1.1`). Это сохраняет честную параметризацию по `AIRFLOW_VERSION`.

`pyspark_pi.py` переиспользуется из spark-образа, а не пишется заново.

ENV образа: `JAVA_HOME` (java-11), `SPARK_HOME=/opt/spark`, `HADOOP_HOME=/opt/hadoop`,
`HADOOP_CONF_DIR=/opt/hadoop/etc/hadoop`, `PATH` += `$SPARK_HOME/bin:$HADOOP_HOME/bin`.

## 4. Сервисы compose

БД Airflow живёт в **существующем** контейнере `hadoop-postgres` (отдельный контейнер БД не заводим).

| Сервис | Роль |
| --- | --- |
| `airflow-init` | one-shot: идемпотентно создаёт роль/БД `airflow` → `airflow db init` → `airflow users create` (admin/admin, если ещё нет). `restart: "no"` |
| `airflow-webserver` | `command: webserver`, порт `8080:8080`, healthcheck `curl --fail http://localhost:8080/health` |
| `airflow-scheduler` | `command: scheduler`, `AIRFLOW__SCHEDULER__ENABLE_HEALTH_CHECK=true`, healthcheck `curl --fail http://localhost:8974/health` |
| `airflow-image` | build-only сервис под `profiles: ["build"]`, по образцу `base` / `spark-image` |

Executor — `LocalExecutor`. Redis, celery-worker, flower, triggerer не заводятся.

`container_name` по единому образцу стенда: `hadoop-airflow-init`, `hadoop-airflow-webserver`,
`hadoop-airflow-scheduler`. `hostname` — `airflow-webserver` / `airflow-scheduler`.

**Создание БД — не через `docker-entrypoint-initdb.d`.** Initdb-скрипты Postgres отрабатывают только при первом
создании volume'а, поэтому на уже существующих стендах Airflow молча не получил бы базу без `--clean`. Вместо
этого `airflow-init` первым шагом идемпотентно создаёт роль и БД через `psycopg2` (пакет уже есть в образе
Airflow), подключаясь к `hadoop-postgres` под `hive/hive`. Описание сервиса `postgres` в compose не меняется.

Порт 8080 в стенде свободен (nginx-proxy занимает 8088, 8042, 9870, 9864, 8188, 10002, 9999, 18080).

## 5. Конфигурация Airflow

Переменные окружения сервисов:

```
AIRFLOW__CORE__EXECUTOR=LocalExecutor
AIRFLOW__DATABASE__SQL_ALCHEMY_CONN=postgresql+psycopg2://airflow:airflow@postgres/airflow
AIRFLOW__CORE__LOAD_EXAMPLES=false
AIRFLOW__CORE__LOAD_DEFAULT_CONNECTIONS=false
AIRFLOW__CORE__DAGS_ARE_PAUSED_AT_CREATION=false
AIRFLOW__CORE__FERNET_KEY=''
AIRFLOW__SCHEDULER__ENABLE_HEALTH_CHECK=true
AIRFLOW_CONN_SPARK_YARN=spark://yarn?deploy-mode=cluster&spark-binary=spark-submit
HADOOP_USER_NAME=hadoop
HADOOP_CONF_DIR=/opt/hadoop/etc/hadoop
SPARK_HOME=/opt/spark
```

- Коннекшен задаётся переменной окружения (`AIRFLOW_CONN_SPARK_YARN`) — состояние не размазывается по БД.
  `conn.host = "yarn"`, порт не задан → master = `yarn`, `_is_yarn = True`.
- `HADOOP_USER_NAME=hadoop` обязателен: контейнер Airflow работает под uid 50000, без переменной запись в HDFS
  пойдёт от несуществующего пользователя.
- Монтируются `./airflow/dags`, `./airflow/jobs`, `./airflow/logs` — правка DAG'ов и джоб не требует пересборки
  образа.

**`deploy-mode=cluster` выбран сознательно.** `spark/config/spark-defaults.conf` задаёт
`spark.pyspark.python=/opt/python/bin/python3` — этого пути в образе Airflow нет, в client-mode драйвер упал бы.
В cluster-mode драйвер уезжает в YARN-контейнер, где путь валиден. Побочный полезный эффект: только при
`yarn` + `cluster` хук парсит YARN application id и умеет корректно убивать приложение.

## 6. DAG'и и джобы

`airflow/dags/spark_pi_dag.py`

```python
submit_pi = SparkSubmitOperator(
    task_id="submit_pi",
    conn_id="spark_yarn",
    application="/opt/airflow/jobs/pyspark_pi.py",
    application_args=["50"],
    name="airflow_spark_pi",
)
```

`airflow/dags/spark_etl_dag.py` — две таски цепочкой `generate >> aggregate`:

- `airflow/jobs/etl_generate.py` — генерирует датасет и пишет `hdfs:///user/hadoop/airflow_demo/raw.parquet`;
- `airflow/jobs/etl_aggregate.py` — читает `raw.parquet`, агрегирует, пишет `.../agg.parquet`.

Оба DAG'а: `schedule=None`, `catchup=False`, `tags=["spark", "demo"]`, `start_date` — фиксированная дата в
прошлом. Никаких параметров, которых нет в сигнатуре провайдера 4.1.1 (см. §2).

Lineage `raw.parquet → agg.parquet` попадает в Marquez штатным OpenLineage-листенером из `spark-defaults.conf`,
namespace `hadoop-cluster`. Отдельная интеграция Airflow↔OpenLineage не ставится.

## 7. Интеграция со скриптами стенда

- `env_example`: `AIRFLOW_VERSION=2.6.3`.
- `docker-compose.yml`: `AIRFLOW_VERSION` в якорь `x-versions`.
- `scripts/image-tags.ps1`: `AIRFLOW_VERSION` в `$required`; `AIRFLOW_IMAGE=hadoop-cluster-airflow:latest`;
  `AIRFLOW_REMOTE=$Registry/hadoop-airflow:a<AIRFLOW_VERSION>-s<SPARK_VERSION>`.
- `start-cluster.bat`: `airflow` в pull-стадию и в стадию сборки уровня jupyter/kyuubi (образ зависит от
  `hadoop-cluster-spark` и `hadoop-cluster-base`), в список verify, и `http://localhost:8080` в финальный вывод.
- `scripts/push-images.ps1`: airflow в список публикуемых образов.
- `airflow/.dockerignore` по образцу `jupyter/.dockerignore`.
- `README.md`: раздел про Airflow, учётка admin/admin, оговорка про размер образа и неиспользуемый `pyspark`.

## 8. Тесты

Каждая задача плана — RED → GREEN. Интеграционный прогон: `tests/test-airflow.bat`

1. контейнеры `hadoop-airflow-webserver` / `hadoop-airflow-scheduler` в состоянии healthy;
2. `airflow dags list-import-errors` пуст;
3. `airflow dags list` содержит `spark_pi_dag` и `spark_etl_dag`;
4. `airflow dags test spark_pi_dag <date>` завершается успешно;
5. `yarn application -list -appStates FINISHED` содержит приложение с именем `airflow_spark_pi`;
6. `airflow dags test spark_etl_dag <date>` успешен, `hdfs dfs -ls` показывает `raw.parquet` и `agg.parquet`;
7. `GET http://localhost:5000/api/v1/namespaces/hadoop-cluster/datasets` содержит оба датасета.

## 9. Осознанно принятые компромиссы

- **`/opt/hadoop` копируется в образ (+~700 МБ).** Бинарь `yarn` нужен хуку в `on_kill` (`yarn application
  -kill`); без него ручная отмена таски падает с `FileNotFoundError`. Размер для локального стенда приемлем.
- **Провайдер тянет `pyspark==3.4.1` (~300 МБ) транзитивно.** В рантайме не используется: submit идёт бинарём
  из `SPARK_HOME=/opt/spark` (Spark 3.5.2). Расхождение версий безвредно, отмечается в README.
- **Airflow-уровневый OpenLineage не ставится.** Lineage покрывается Spark-листенером; Airflow-события в
  Marquez — отдельная задача.
- **Учётка admin/admin и пустой Fernet-ключ.** Это локальный тест-стенд без внешнего доступа, как и остальные
  сервисы (hive/hive, marquez/marquez).
