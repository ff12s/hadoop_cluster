# OpenLineage: инъекция per-runtime вместо глобального spark-defaults.conf

**Дата:** 2026-07-23
**Статус:** утверждён (brainstorming), готов к плану реализации
**Артефакт:** `spark/config/spark-defaults.conf`, `airflow/config/airflow_local_settings.py` (новый), `docker-compose.yml`, `jupyter/scripts/start-jupyter.sh`, `kyuubi/config/kyuubi-defaults.conf`, `.env` / `env_example`, `README.md`
**Репозиторий:** `hadoop_cluster` (изменения только здесь)

## 1. Контекст и цель

OpenLineage-листенер сейчас включён **глобально** через `spark/config/spark-defaults.conf` —
единственный host-файл, примонтированный `:ro` сразу в четыре сервиса (`airflow`, `hadoop`,
`jupyter`, `kyuubi`). Из-за этого `spark.extraListeners=…OpenLineageSparkListener` навешивается на
**любой** Spark-entrypoint, читающий этот файл, включая интерактивный `spark-shell`. Для `spark-shell`
это ломает запуск: листенер инициализируется, пытается достучаться до Marquez, REPL падает/висит.

Цель — убрать OL-конфиг из общего `spark-defaults.conf` и инжектить его **точечно, на стороне каждого
рантайма, который должен писать лайнидж** (Airflow, Jupyter, Kyuubi). Фактический лайнидж этих трёх не
меняется; `spark-shell` и «голая» нода `hadoop`/history перестают грузить листенер.

Исходная идея пользователя — «сделать через дефолтный Spark connection» — не реализуема: провайдер
`apache-airflow-providers-apache-spark` читает из extra коннекшена фиксированный набор ключей
(`deploy-mode`, `spark-binary`, `namespace`, `queue`, `spark-home`) и не переносит произвольный
`--conf`. Airflow-native эквивалент «задать один раз для всех Spark-джоб» — **cluster policy**
(`task_policy` в `airflow_local_settings.py`).

**Не входит в скоуп:** per-task parent-run линковка (`spark.openlineage.parent*`, иерархия job'ов в
Marquez — требует Airflow ≥ 2.7 и провайдер `apache-airflow-providers-openlineage`); подключение
namespace-resolver (`spark.openlineage.dataset.namespaceResolvers.*` — конфигурируется только в
`tests/test-namespace-resolver.sh`, в основном OL-блоке его нет); изменения в соседнем репозитории
`SparkAPI`.

## 2. Грундинг-бриф — обязателен во всех брифах реализации

Пины: Airflow **2.6.3**, OpenLineage **1.46.0**, Marquez **0.47.0**, провайдер
`apache-airflow-providers-apache-spark` (установлен `--no-deps`, без `pyspark`).

| Факт | Источник |
| --- | --- |
| Spark-коннекшен читает из extra фиксированный набор ключей (`deploy-mode`, `spark-binary`, `namespace`, `queue`, `spark-home`); механизма протащить произвольный `spark --conf` через коннекшен нет. `--conf` доходит до spark-submit только через `spark-defaults.conf`, параметр оператора `conf={…}`, либо env | context7 `/websites/airflow_apache_registry_providers`, query `"SparkSubmitOperator/Hook connection extra keys + arbitrary spark --conf"` |
| Cluster policy `task_policy(task) -> None` объявляется в `airflow_local_settings.py`, который автоматически импортируется с `$AIRFLOW_HOME/config` (у образа `AIRFLOW_HOME=/opt/airflow` → `/opt/airflow/config/airflow_local_settings.py`); функция мутирует task in-place на этапе парсинга DAG. Классическая форма (module-level `def task_policy(task)`) поддерживается в 2.6.x | context7 `/apache/airflow/2_7_3`, query `"cluster policy task_policy in airflow_local_settings.py: file location on PYTHONPATH, function signature, mutating operator attributes"` (дельта: снапшота 2.6.x в context7 нет, использован ближайший 2.7.3; классическая форма проверяется на этапе плана) |
| Автоинъекция `spark_inject_parent_job_info` / макросы `macros.OpenLineageProviderPlugin.lineage_*` — фичи провайдера `apache-airflow-providers-openlineage`, требуют Airflow ≥ 2.7 → на 2.6.3 недоступны | context7 `/openlineage/openlineage`, query `"Airflow parent job injection SparkSubmitOperator"` |
| Канонический OL-конфиг Spark-листенера: `spark.extraListeners=io.openlineage.spark.agent.OpenLineageSparkListener`, `spark.openlineage.transport.type=http`, `spark.openlineage.transport.url`, `spark.openlineage.namespace`. `spark.extraListeners` **не аддитивен** — переопределение сбрасывает прочие листенеры | context7 `/openlineage/openlineage`, query `"OpenLineage Spark listener configuration conf keys, spark-defaults.conf, http transport"` |
| Настройка через `spark-defaults.conf` в `$SPARK_HOME/conf` применяется ко **всем** Spark-процессам образа (в т.ч. `spark-shell`); чтобы ограничить — конфиг задаётся не в defaults-файле, а на стороне конкретного запуска (`--conf`, `PYSPARK_SUBMIT_ARGS`, engine-defaults) | context7 `/openlineage/openlineage`, query как выше |

## 3. Канонический набор OL-ключей

Пять строк, которые сейчас лежат в `spark-defaults.conf` (строки 14–24) и переезжают per-runtime:

```
spark.extraListeners=io.openlineage.spark.agent.OpenLineageSparkListener
spark.openlineage.transport.type=http
spark.openlineage.transport.url=<OPENLINEAGE_URL>
spark.openlineage.namespace=<OPENLINEAGE_NAMESPACE>
spark.openlineage.columnLineage.datasetLineageEnabled=true
```

## 4. Изменения по компонентам

### 4.1. `spark/config/spark-defaults.conf` — убрать OL-блок
Удалить строки 14–24 (комментарии + пять OL-ключей). Базовый тюнинг Spark (master, HDFS-адреса,
eventLog, ресурсы, python-пути) остаётся без изменений. Это единственная правка, чинящая `spark-shell`
и снимающая листенер с ноды `hadoop`/spark-history.

### 4.2. Источник конфига — env
В `.env` и `env_example` добавить `OPENLINEAGE_URL=http://marquez:5000`; в docker-compose добавить
`OPENLINEAGE_URL: ${OPENLINEAGE_URL}` в anchor `x-versions: &versions` (его `<<: *versions` уже тянут
сервисы `airflow` — через `*airflow-env` — и `jupyter`). `OPENLINEAGE_NAMESPACE` уже прокинут через
`*versions`.

### 4.3. Airflow — cluster policy (выбранный механизм)
Новый файл `airflow/config/airflow_local_settings.py` с module-level `task_policy(task)`: ленивый импорт
`SparkSubmitOperator`, для подходящих тасок мержит OL-словарь в `task.conf`. Значения читаются из
`os.environ` (`OPENLINEAGE_URL`, `OPENLINEAGE_NAMESPACE`) с дефолтами.

```python
import os


def task_policy(task) -> None:
    from airflow.providers.apache.spark.operators.spark_submit import SparkSubmitOperator
    if not isinstance(task, SparkSubmitOperator):
        return
    ol = {
        "spark.extraListeners": "io.openlineage.spark.agent.OpenLineageSparkListener",
        "spark.openlineage.transport.type": "http",
        "spark.openlineage.transport.url": os.environ.get("OPENLINEAGE_URL", "http://marquez:5000"),
        "spark.openlineage.namespace": os.environ.get("OPENLINEAGE_NAMESPACE", "hadoop-cluster"),
        "spark.openlineage.columnLineage.datasetLineageEnabled": "true",
    }
    task.conf = {**ol, **(task.conf or {})}
```

Порядок мержа `{**ol, **(task.conf or {})}` — conf, заданный в DAG, побеждает. Задокументировать
(README + комментарий), что DAG'ам не следует переопределять `spark.extraListeners` (не аддитивен —
собьёт OL). Монтирование в docker-compose (в `x-airflow-volumes: &airflow-volumes`):
`./airflow/config/airflow_local_settings.py:/opt/airflow/config/airflow_local_settings.py:ro`. Оба демо-DAG
(`spark_pi_dag`, `spark_etl_dag`) наследуют OL без правок.

### 4.4. Jupyter — `PYSPARK_SUBMIT_ARGS`
В `jupyter/scripts/start-jupyter.sh` экспортировать `PYSPARK_SUBMIT_ARGS` с OL-ключами через `--conf` и
обязательным хвостом `pyspark-shell`. Применяется к SparkSession ноутбуков (pyspark поднимает JVM через
py4j и потребляет `PYSPARK_SUBMIT_ARGS`); Scala `spark-shell` его не читает. URL/namespace из
`$OPENLINEAGE_URL` / `$OPENLINEAGE_NAMESPACE` (сервис `jupyter` уже получает их из `*versions` после §4.2).

### 4.5. Kyuubi — `kyuubi-defaults.conf`
Дописать OL-ключи `spark.*` прямо в `kyuubi/config/kyuubi-defaults.conf`. Kyuubi пробрасывает `spark.*`
из своего defaults в порождаемый Spark-engine. URL **хардкодится** `http://marquez:5000`, namespace —
`hadoop-cluster` (консистентно с уже захардкоженными в этом файле `namenode:9000` и т.п.; файл статичный,
env не подставляется). Конфиг запечён в образ → нужна пересборка kyuubi-образа. Сервис `kyuubi` уже
`depends_on: marquez`.

### 4.6. Документация
Обновить в `README.md` раздел про OpenLineage/Marquez: OL больше не в общем `spark-defaults.conf`, а
инжектится per-runtime (Airflow cluster policy, Jupyter `PYSPARK_SUBMIT_ARGS`, Kyuubi `kyuubi-defaults`);
`spark-shell` OL не грузит; добавлена переменная `OPENLINEAGE_URL`. CHANGELOG в репозитории отсутствует —
не заводим.

## 5. Поток данных

Без изменений по сути: каждый из трёх рантаймов запускает Spark-приложение на YARN, листенер шлёт OL-события
на `http://marquez:5000`, Marquez пишет их в свой PostgreSQL, Web-UI (`:3000`) показывает лайнидж в
namespace `hadoop-cluster`. Меняется только **откуда** берётся конфиг листенера (per-runtime вместо общего
defaults-файла) и **кто** его больше не получает (`spark-shell`, нода `hadoop`).

## 6. Обработка ошибок / краевые случаи

- **`spark.extraListeners` не аддитивен.** Если DAG задаёт свой `conf["spark.extraListeners"]`, cluster
  policy его не перезапишет (DAG побеждает) и OL для этой таски пропадёт. Приемлемо для стенда;
  задокументировано.
- **Marquez недоступен на старте Kyuubi.** Уже покрыто `depends_on: marquez`; на уровне джобы падение
  транспорта OL не должно валить сам Spark-джоб (поведение как было при конфиге в spark-defaults).
- **`airflow_local_settings.py` с синтаксической ошибкой** уронит парсинг всех DAG. Минимизируется тем,
  что файл крошечный и покрыт тестом (§7).

## 7. Тестирование / приёмка

- `spark-shell` внутри кластера (`docker exec …`) стартует чисто: листенер OL не грузится, обращений к
  Marquez нет. **Ключевой критерий — исходная проблема закрыта.**
- Триггер `spark_etl_dag` → в Marquez namespace `hadoop-cluster` появляется лайнидж
  `airflow_etl_generate` → `airflow_etl_aggregate` (input/output). Триггер `spark_pi_dag` → джоба видна.
- Jupyter-ноутбук со Spark-джобой и Kyuubi SQL-запрос → по-прежнему появляются в Marquez.
- Существующие `tests/test-openlineage.bat`, `tests/test-airflow.bat` проходят (плюс jupyter/kyuubi тесты,
  если есть).
- Unit-проверка `task_policy`: для инстанса `SparkSubmitOperator` в `task.conf` попадают OL-ключи; для
  не-Spark таски conf не трогается; conf, заданный в DAG, побеждает.

## 8. Затронутые файлы (сводка)

| Файл | Действие |
| --- | --- |
| `spark/config/spark-defaults.conf` | убрать OL-блок (стр. 14–24) |
| `airflow/config/airflow_local_settings.py` | **новый** — cluster policy `task_policy` |
| `docker-compose.yml` | mount local-settings в `airflow`; `OPENLINEAGE_URL` в `x-versions` |
| `jupyter/scripts/start-jupyter.sh` | export `PYSPARK_SUBMIT_ARGS` с OL-ключами |
| `kyuubi/config/kyuubi-defaults.conf` | дописать OL `spark.*` (URL хардкод) |
| `.env`, `env_example` | `OPENLINEAGE_URL=http://marquez:5000` |
| `README.md` | обновить раздел OpenLineage |