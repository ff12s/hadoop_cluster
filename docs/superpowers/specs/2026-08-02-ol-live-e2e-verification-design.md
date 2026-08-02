# Живая E2E-проверка ol_policy и openlineage-namespace-resolver

Дата: 2026-08-02
Ветка: `feature/openlineage-per-runtime-injection`

## Задача

Проверить на работающем Airflow две вещи, которые до сих пор проверялись только юнит-тестами:

1. cluster policy `airflow/config/ol_policy` действительно доводит OpenLineage до Spark-джобы и
   события долетают до Marquez;
2. `openlineage-namespace-resolver` собирается, подхватывается драйвером на YARN и чинит
   namespace, который Marquez отвергает.

Контур: Marquez 0.47.0, openlineage-spark 1.46.0 (scala 2.13). Airflow проверяется в двух
вариантах зависимостей: `airflow/requirements.txt` (2.6.3) и `airflow/requirements_cloud.txt`
(2.10.2).

## Что установила разведка

- Стенд уже пинует нужные версии: `docker-compose.yml` тянет `marquezproject/marquez:0.47.0`,
  `.env` задаёт `OPENLINEAGE_VERSION=1.46.0` и
  `OPENLINEAGE_JAR=hdfs://namenode:9000/opt/openlineage/openlineage-spark_2.13-1.46.0.jar`.
- Resolver **нигде не подключён**. Поиск `namespaceResolvers` по `*.conf`, `*.py`, `*.sh`,
  `*.bat`, `*.yml` не даёт ни одного попадания вне `.venv`. Jar `spark/jars/openlineage-namespace-resolver.jar`
  протух (собран 18 июля) и монтируется только в `jupyter`.
- `ol_policy` не может доставить конфиг резолвера: `callback._write_lineage` пишет
  фиксированный набор из четырёх ключей, а лишние ключи `spark_conf` из Variable отбрасываются.
  Поле `openlineage_jar` — одна строка, второго jar'а в `--jars` не положить.
- `airflow/Dockerfile` не использует ни один из файлов реквайрментов: провайдер ставится по
  constraints-файлу Airflow.
- `airflow/scripts/start-airflow.sh:11` выполняет `airflow db init` — написание, существующее
  только в 2.6.x.
- JDBC-драйвера PostgreSQL в spark-образе нет; в hive-образе лежит
  `/opt/hive/lib/postgresql-42.2.23.jar`.

## Grounding

Все запросы выполнены через context7 2026-08-02.

- `/openlineage/openlineage`, запрос `"spark.openlineage.dataset.namespaceResolvers configuration
  custom DatasetNamespaceResolverBuilder"` → `website/docs/client/java/partials/java_namespace_resolver.md`:
  «Custom namespace resolvers can be implemented by creating classes that extend
  `DatasetNamespaceResolver`, `DatasetNamespaceResolverBuilder`, and `DatasetNamespaceResolverConfig`.
  … Custom resolvers are loaded using the `ServiceLoader` approach.» Форма ключа —
  `spark.openlineage.dataset.namespaceResolvers.<name>.type`. Механизм появился в 1.17.1.
- `/openlineage/openlineage`, запрос `"openlineage-spark best practices pitfalls extraListeners
  jars deploy-mode cluster on YARN"` → `integrations/spark/configuration/usage.md`:
  «`spark.extraListeners` is non-additive and will replace existing values.» Подтверждает, что
  слияние через `utils.merge_csv` обязательно, перезапись недопустима.
- `/marquezproject/marquez`, запрос `"REST API endpoints list namespaces jobs runs and lineage
  events"` → `GET /api/v1/namespaces`, `GET /api/v1/namespaces/{ns}/jobs`,
  `GET /api/v1/lineage?nodeId=job:<ns>:<name>&depth=N`,
  `GET /api/v1/events/lineage?after=<ISO8601>&limit=N`. Это поверхность ассертов.
- `/apache/airflow/2.10.5`, запрос `"cluster policy task_policy in airflow_local_settings and
  on_execute_callback list of callables"` → `cluster-policies.rst`: `task_policy(task)` в 2.10
  не изменился, по-прежнему «executed when the task is created during parsing from DagBag at load
  time».
- `/apache/airflow/2.10.5`, запрос `"openlineage provider configuration disable and
  SparkSubmitOperator parent job information injection"` → `providers-openlineage/guides/user.rst`:
  выключение нативного провайдера через `AIRFLOW__OPENLINEAGE__DISABLED=true`;
  `providers-openlineage/macros.rst`: фреймворк штатно предлагает
  `macros.OpenLineageProviderPlugin.lineage_job_namespace() / lineage_job_name(ti) /
  lineage_run_id(ti)` для проброса parent-run в conf `SparkSubmitOperator`.

Замечание по версиям: снапшота 2.6.3 у context7 нет, ближайший — 2.10.5; для cloud-варианта это
совпадение, для base-варианта дельта названа явно.

## Решения

### 1. Два варианта образа Airflow

`airflow/Dockerfile` получает `ARG AIRFLOW_REQUIREMENTS`. Файл копируется в образ и ставится
`pip install -r` с `--constraint` под соответствующую версию Airflow.

| вариант | базовый образ | requirements | тег |
|---|---|---|---|
| `base` | `apache/airflow:2.6.3-python3.10` | `airflow/requirements_slim.txt` | `hadoop-cluster-airflow:2.6.3` |
| `cloud` | `apache/airflow:2.10.2-python3.10` | `airflow/requirements_cloud_slim.txt` | `hadoop-cluster-airflow:2.10.2` |

Оба варианта ставят **срез**, значимый для пути `ol_policy`: ядро Airflow, провайдеры
`apache-spark`, `postgres`, `celery`, `fab` (только для 2.10), `openlineage` (только для cloud), их
транзитивные зависимости. Версии берутся дословно из соответствующего исходного файла, срез
фиксируется в отдельном `*_slim.txt` с шапкой, объясняющей критерий отбора.

Причина среза: полные файлы тянут `cx-Oracle`, `pymssql`, `PyHive`, `hmsclient`, `gssapi`,
`confluent-kafka`, `xmlsec`, `pygraphviz`, `mysqlclient` — им нужны системные `-dev` пакеты,
отсутствующие в базовом образе Airflow, и ни один из них не участвует в пути cluster policy.
`requirements_cloud.txt` вдобавок содержит 737 пинов (aws + azure + gcp + beam + snowflake +
dev-тулинг), что даёт многогигабайтный образ.

Compose получает сервис сборки `airflow-image-cloud` в профиле `build`. Рантайм-сервис `airflow`
продолжает брать тег из `${AIRFLOW_IMAGE}` — переключение варианта делается переменной окружения,
без правки compose.

`airflow/scripts/start-airflow.sh` выбирает команду миграции по версии Airflow: `db migrate` для
2.7 и новее, `db init` иначе. Cloud-вариант дополнительно получает
`AIRFLOW__OPENLINEAGE__DISABLED=true`, чтобы нативный провайдер openlineage 1.11.0 не слал
собственный поток событий: сравнение вариантов должно быть like-for-like, а различить два
источника событий в Marquez сложнее, чем погасить один.

### 2. `ol_policy`: сквозная передача `spark_conf` и CSV в поле jar'а

`variable.py`:

- `Config` получает поле `extra_conf: dict[str, str]` — все ключи `spark_conf`, кроме четырёх
  валидируемых. Ключи, которыми владеет стенд (`spark.master`, `spark.submit.deployMode`),
  отбрасываются.
- `jar_uri: str` заменяется на `jar_uris: tuple[str, ...]`; значение поля `openlineage_jar`
  разбирается как CSV, каждый элемент обязан иметь схему и путь.
- `validate_config` по-прежнему требует все четыре обязательных значения. Расширяется только то,
  что сверх них.

`callback.py::_write_lineage` собирает итоговый conf в порядке: DAG-conf → `extra_conf` из
Variable → четыре валидированных override'а. Variable перекрывает DAG, обязательная четвёрка
перекрывает всё. Хардкод `spark.openlineage.columnLineage.datasetLineageEnabled` из кода убирается:
ключ уже лежит в Variable и дублировался.

`probe.py`: `jar_path` и `jar_available` работают по каждому URI кортежа. Недоступность любого из
jar'ов гасит лайнидж с warning'ом, называющим конкретный URI.

Цена решения названа явно: опечатка в `spark_conf` теперь молча доезжает до `spark-submit`.
Раньше лишний ключ отбрасывался. Это осознанный размен на возможность включать новые OL-ключи
правкой Variable в UI, без деплоя кода.

Юнит-тесты `airflow/config/tests/test_ol_policy.py` расширяются под новую форму по циклу
RED → GREEN → REFACTOR.

### 3. Resolver: сборка, доставка, включение

Сборка: `bash mvnd.sh test`, затем `bash mvnd.sh package`. Полученный
`target/openlineage-namespace-resolver-0.1.0.jar` заменяет протухший `spark/jars/openlineage-namespace-resolver.jar`.

`scripts/seed-openlineage-jar.bat` расширяется и заливает в HDFS `/opt/openlineage/` три jar'а:

- `openlineage-spark_2.13-1.46.0.jar` — как сейчас, из `/opt/spark/jars` контейнера `hadoop-node`;
- `openlineage-namespace-resolver.jar` — с хоста через `docker cp` в `hadoop-node`, оттуда `hdfs dfs -put`;
- `postgresql-42.2.23.jar` — из `/opt/hive/lib/` контейнера `hadoop-hive`.

Сид Variable `openlineage_config` в `start-airflow.sh` становится:

```json
{
  "enabled": true,
  "spark_conf": {
    "spark.extraListeners": "io.openlineage.spark.agent.OpenLineageSparkListener",
    "spark.openlineage.transport.type": "http",
    "spark.openlineage.transport.url": "http://marquez:5000",
    "spark.openlineage.namespace": "hadoop-cluster",
    "spark.openlineage.columnLineage.datasetLineageEnabled": "true",
    "spark.openlineage.dataset.namespaceResolvers.default.type": "normalize"
  },
  "openlineage_jar": "hdfs://namenode:9000/opt/openlineage/openlineage-spark_2.13-1.46.0.jar,hdfs://namenode:9000/opt/openlineage/openlineage-namespace-resolver.jar"
}
```

В общий `spark/config/spark-defaults.conf` не добавляется ничего: коммитнутый conf остаётся без
OpenLineage, иначе `spark-shell` и прочие джобы стенда начнут требовать jar, которого у них нет.

### 4. Доказательный DAG

Текущие DAG'и пишут в HDFS и Hive, namespace `hdfs://namenode:9000` валиден для Marquez сам по
себе — resolver на них ничего не меняет, и прогон был бы зелёным и без него. Нужен датасет с
namespace, который Marquez 0.47.0 отвергает.

`airflow/jobs/etl_jdbc_multihost.py` — PySpark-джоба: пишет синтетическую таблицу в PostgreSQL
через JDBC, читает её обратно, агрегирует, пишет parquet в HDFS. JDBC URL — multi-host:
`jdbc:postgresql://postgres:5432,marquez-db:5432/hive_metastore`. Оба хоста указывают на один и тот
же контейнер `hadoop-postgres` через два network-алиаса, которые compose уже выдаёт
(`docker-compose.yml`, `networks.default.aliases` сервиса `postgres`), поэтому подключение реально
работает. OpenLineage при этом строит namespace `postgres://postgres:5432,marquez-db:5432` — с
запятой, которой нет в charset Marquez `^[a-zA-Z0-9_@+:;=/.-]{1,1024}$`.

`airflow/dags/spark_jdbc_lineage_dag.py` — один `SparkSubmitOperator` с непустым `jars=` (JDBC-драйвер
из HDFS). Непустой DAG-level `jars` заодно покрывает ветку
`utils.merge_csv(getattr(task, attrs.jars), dag_conf_jars, ...)`, которая живьём не проверялась ни
разу.

Ожидаемое поведение:

- без resolver-jar — Marquez отвечает 400 на событие, датасет в графе отсутствует;
- с resolver — namespace `postgres://marquez-db:5432+postgres:5432` (хосты отсортированы,
  склеены `+`), датасет присутствует в графе, `GET /api/v1/namespaces` возвращает 200.

### 5. Харнесс `tests/live/`

pytest-сьют, запускаемый хостовым `.venv` (Python 3.8.10, pytest 7.4.0, requests 2.31.0).

- `tests/live/conftest.py` — фикстуры: базовый URL Marquez, `variant` из переменной окружения
  `OL_E2E_VARIANT` (`base` или `cloud`), `dag_run` (триггер DAG'а и поллинг до финального
  состояния через `docker exec hadoop-airflow airflow dags ...`). Весь модуль скипается, если
  Marquez или контейнер недоступны — так же, как ведут себя остальные тесты стенда.
- `tests/live/test_ol_policy_e2e.py` — прогон `spark_etl_dag`: обе таски в состоянии `success`; в
  Marquez есть namespace `hadoop-cluster`, джобы `airflow_etl_generate` и `airflow_etl_aggregate`,
  а `GET /api/v1/lineage?nodeId=job:hadoop-cluster:...` даёт связку input → output.
- `tests/live/test_namespace_resolver_e2e.py` — два прогона `spark_jdbc_lineage_dag`: сначала
  негативный контроль (resolver убран из Variable), затем положительный. Ассерты строятся по
  `GET /api/v1/events/lineage?after=<T0>` и `GET /api/v1/namespaces`.

Существующий `tests/test-namespace-resolver.sh` не изменяется: он проверяет jupyter-путь, то есть
другой рантайм.

### 6. Порядок прогона и критерий приёмки

1. `bash mvnd.sh test` — юнит- и интеграционные тесты resolver'а зелёные.
2. `start-cluster.bat --clean --build` в варианте `base`.
3. Сид трёх jar'ов в HDFS.
4. `pytest tests/live` с `OL_E2E_VARIANT=base`.
5. Сборка cloud-образа, пересоздание сервиса `airflow` с ним, миграция схемы.
6. `pytest tests/live` с `OL_E2E_VARIANT=cloud`.
7. Отчёт о прогоне и запись в `CHANGELOG.md`.

**Проверено** = все семь шагов зелёные на обоих вариантах, причём негативный контроль резолвера в
обоих вариантах действительно красный при отсутствии jar'а. Падение на любом шаге — находка,
которую нужно чинить и перепрогонять, а не повод сузить проверку.

## Отвергнутые варианты

- **Полный E2E только на 2.6.3, для cloud — проверка совместимости без spark-submit.** Дешевле, но
  не доказывает, что лайнидж долетает на 2.10.2.
- **Оставить нативный провайдер openlineage включённым в cloud-варианте.** Даёт больше покрытия, но
  требует различать в Marquez два источника событий; отложено.
- **Отдельные поля Variable `resolver_jar` и зашитый ключ `namespaceResolvers`.** Строже валидация,
  но каждый новый OL-ключ снова требует правки кода.
- **Шейдить resolver в общий fat-jar с openlineage-spark.** Расходится с продом, где используется
  стоковый `openlineage-spark` из Maven Central, и ломает обновление OL без пересборки.
- **Доказывать resolver прямым POST события в Marquez через curl.** Не проверяет главного: что
  ServiceLoader находит наш builder в драйвере на YARN при jar'е, приехавшем из HDFS.
- **Bash-скрипт `tests/test-ol-airflow-e2e.sh` вместо pytest.** Ближе к конвенции каталога
  `tests/`, но ассерты по JSON через `jq`/`grep` заметно хуже читаются.

## Риски

- ServiceLoader должен найти builder резолвера в драйвере на YARN при jar'е, приехавшем из HDFS
  через `--jars`. Механизм тот же, что у openlineage-spark jar, но живьём не проверялся.
- Сборка образа Airflow 2.10.2 поверх донорских стейджей Spark/Hadoop может потребовать правок:
  Java 11 в базе есть, но `db migrate` и провайдер FAB — новые для стенда.
- pgjdbc 42.2.23 поддерживает failover-URL с несколькими хостами; проверяется на первом прогоне.
- Срез реквайрментов может разойтись с полным файлом по транзитивным версиям. Каждый `*_slim.txt`
  пинует версии дословно из источника, но полный граф зависимостей не воспроизводится.
