# Hadoop Cluster для тестирования

Тестовый кластер Hadoop с полным стеком технологий для разработки и тестирования Big Data приложений.

## Компоненты кластера

- **Hadoop 3.3.6** — HDFS, YARN, MapReduce, Timeline Server
- **Hive 3.1.3** — Data Warehouse с PostgreSQL 13 и движком **Apache Tez**
- **Apache Tez 0.10.2** — DAG-движок для Hive (замена MapReduce), с Tez UI
- **Spark 3.5.2** — Обработка данных и машинное обучение
- **JupyterLab** — Интерактивная разработка с PySpark и Scala (опционально, профиль `jupyter`)
- **Kyuubi 1.10.2** — Spark SQL через JDBC/Thrift (опционально, профиль `kyuubi`)
- **Airflow 2.6.3** — Оркестрация Spark-джоб на YARN
- **OpenLineage** — Трассировка данных (Marquez)
- **Nginx** — Реверс-прокси для всех веб-интерфейсов
- **Java 8**, **Python 3.12**, **Scala 2.13.8**

## Быстрый старт

### Предварительные требования

- Docker Desktop (рекомендуется 8 GB+ RAM)
- Docker Compose

### Запуск кластера

```bash
# Полный запуск: pull из Docker Hub, при отсутствии тега — build, затем запуск + health-check.
# Поднимает семь основных сервисов; kyuubi и jupyter остаются выключенными.
start-cluster.bat

# Полный запуск с очисткой volumes
start-cluster.bat --clean

# Дополнительно поднять опциональные сервисы
start-cluster.bat --with-kyuubi
start-cluster.bat --with-jupyter
start-cluster.bat --all

# Остановка
docker compose stop

# Остановка с удалением контейнеров
docker compose down
```

> ⚠️ **При переходе со старой раскладки стенда требуется разовый `start-cluster.bat --clean`.**
> Роль и база `marquez` создаются init-скриптом PostgreSQL, а он выполняется только на пустом
> томе данных. Без очистки база не появится и Marquez не поднимется. Очистка стирает HDFS,
> hive warehouse, метаданные Airflow и историю Marquez.

> ⚠️ **Изменили что-то в `base/`, `hive/`, `spark/`, `jupyter/`, `kyuubi/` или `airflow/` —
> переиздайте образы.** Без флага `--build` скрипт тянет готовые образы с Docker Hub и
> **перетирает ими локально собранные теги**. Если опубликованные образы отстали от
> репозитория, стенд падает на старте (например, `stat /opt/scripts/start-hadoop.sh:
> no such file or directory`). Порядок: `start-cluster.bat --build --all`, убедиться что стенд
> поднялся, затем `powershell -File scripts\push-images.ps1`.

### Опциональные сервисы: Kyuubi и Jupyter

По умолчанию поднимаются только семь основных сервисов (`hadoop`, `postgres`, `hive`,
`airflow`, `marquez`, `marquez-web`, `webproxy`) — `kyuubi` и `jupyter` тяжёлые и не
нужны для базового сценария, поэтому вынесены в опциональные compose-профили `kyuubi`
и `jupyter` и по умолчанию не стартуют.

```bash
# Рекомендуемый способ — флаги скрипта запуска
start-cluster.bat --with-kyuubi
start-cluster.bat --all

# Напрямую через compose
docker compose --profile kyuubi --profile jupyter up -d

# То же самое через переменную окружения
COMPOSE_PROFILES=kyuubi,jupyter docker compose up -d
```

> ⚠️ `docker compose down` **не останавливает** контейнеры выключенных профилей —
> это документированное поведение Docker Compose. Если стенд был поднят с профилями,
> гасите его с теми же профилями: `COMPOSE_PROFILES=kyuubi,jupyter docker compose down`.
>
> `start-cluster.bat` эту ловушку закрывает сам: этап остановки он всегда выполняет со
> всеми профилями, поэтому обычный `start-cluster.bat` без флагов снимает и ранее
> поднятые `kyuubi` с `jupyter`, а не оставляет их занимать память.

## Веб-интерфейсы

Все веб-интерфейсы доступны через Nginx реверс-прокси — внутренние hostname контейнеров автоматически заменяются на `localhost`.

> Стенд рассчитан только на локальный запуск: сервисы поднимаются с дефолтными учётками и портами, слушающими все интерфейсы. Не выставляйте его в сеть.

| Сервис | URL | Описание |
|--------|-----|----------|
| HDFS NameNode | http://localhost:9870 | Управление файловой системой |
| HDFS DataNode | http://localhost:9864 | Информация о DataNode |
| YARN ResourceManager | http://localhost:8088 | Управление ресурсами и приложениями |
| YARN NodeManager | http://localhost:8042 | Информация о NodeManager |
| YARN Timeline Server | http://localhost:8188 | История приложений YARN |
| Tez UI | http://localhost:9999 | Мониторинг DAG-задач Tez |
| Spark History Server | http://localhost:18080 | История Spark-приложений |
| HiveServer2 Web UI | http://localhost:10002 | Веб-интерфейс Hive |
| JupyterLab | http://localhost:8888 | Интерактивная разработка (профиль `jupyter`, не поднимается по умолчанию) |
| Airflow | http://localhost:8080 | Оркестрация DAG'ов (учётка по умолчанию `admin` / `admin`) |
| Marquez Web | http://localhost:3000 | Трассировка данных |
| Marquez API | http://localhost:5000 | API для трассировки |

## Архитектура

```
┌───────────────────────────────────────────────────────────────┐
│   Nginx Reverse Proxy (webproxy, отдаёт и статику TEZ UI)     │
│       :9870 :8088 :8188 :9864 :8042 :10002 :9999 :18080       │
└─────────┬───────────────────────────────┬───────────────────────┘
          │                               │
┌───────────────────┐              ┌──────────────────────────┐
│ Hadoop Node       │              │ Hive                     │
│                   │              │ - Metastore :9083        │
│ - NameNode        │              │   (thrift, напрямую,     │
│ - DataNode        │              │    мимо nginx)           │
│ - ResourceManager │              │ - HiveServer2            │
│ - NodeManager     │              └──────────────────────────┘
│ - Timeline Server │
│ - Spark History   │
└───────────────────┘
          │                          │                │
          ▼                          ▼                ▼
      ┌──────┐                  ┌────────┐  ┌──────────────────┐
      │ HDFS │                  │ Kyuubi │  │ PostgreSQL       │
      └──────┘                  │ :10009 │  │                  │
                                └────────┘  │ - hive_metastore │
                                            │ - airflow        │
                                            │ - marquez        │
                                            └──────────────────┘

   ┌────────────┐   ┌─────────────┐
   │ JupyterLab │   │   Marquez   │
   │   :8888    │   │ :3000/:5000 │
   └────────────┘   └─────────────┘
```

## Структура проекта

```
hadoop_cluster/
├── base/                    # Базовый образ Hadoop
│   ├── config/              # core-site, hdfs-site, yarn-site, mapred-site
│   ├── scripts/             # Скрипты запуска и проверки
│   ├── .dockerignore
│   └── Dockerfile
├── hive/                    # Hive + Tez (Metastore + HiveServer2 + Tez UI)
│   ├── config/              # hive-site.xml, tez-site.xml
│   ├── scripts/             # start-hive
│   ├── .dockerignore
│   └── Dockerfile
├── spark/                   # Spark с History Server
│   ├── config/              # spark-defaults.conf, log4j.properties
│   ├── scripts/             # Скрипты запуска и тестирования
│   ├── .dockerignore
│   └── Dockerfile
├── jupyter/                 # JupyterLab
│   ├── notebooks/           # Jupyter ноутбуки
│   ├── scripts/             # Скрипты запуска
│   ├── .dockerignore
│   └── Dockerfile
├── kyuubi/                  # Kyuubi (Spark SQL)
│   ├── config/              # kyuubi-defaults.conf
│   ├── scripts/             # Скрипты запуска
│   ├── .dockerignore
│   └── Dockerfile
├── airflow/                 # Airflow (webserver + scheduler)
│   ├── dags/                # spark_pi_dag, spark_etl_dag
│   ├── jobs/                # PySpark-джобы для DAG'ов
│   ├── config/              # cluster policy: airflow_local_settings.py (точка входа) + пакет ol_policy/, tests/
│   ├── scripts/             # start-airflow.sh, ensure_db.py
│   ├── logs/                # Логи задач (монтируются, не коммитятся)
│   ├── .dockerignore
│   └── Dockerfile
├── marquez/                 # OpenLineage
│   └── config/              # config.yml
├── nginx/                   # Реверс-прокси
│   └── nginx.conf           # Конфигурация проксирования всех UI
├── tests/                   # Тестовые скрипты
├── scripts/                 # Утилиты для тегов и публикации образов
│   ├── image-tags.ps1       # Единый генератор тегов/имен образов
│   └── push-images.ps1      # Tag + push образов в Docker Hub
├── docker-compose.yml       # Конфигурация кластера
├── env_example              # Пример переменных окружения
├── start-cluster.bat        # Скрипт запуска кластера
└── README.md
```

## Конфигурация

### Переменные окружения (.env)

Скопируйте `env_example` в `.env` и при необходимости отредактируйте:

```bash
copy env_example .env
```

#### Версии компонентов
| Переменная | Значение | Описание |
|------------|----------|----------|
| `HADOOP_VERSION` | `3.3.6` | Apache Hadoop |
| `HIVE_VERSION` | `3.1.3` | Apache Hive |
| `TEZ_VERSION` | `0.10.2` | Apache Tez |
| `SPARK_VERSION` | `3.5.2` | Apache Spark |
| `SCALA_VERSION` | `2.13.8` | Scala |
| `PYTHON_VERSION` | `3.12.7` | Python |
| `KYUUBI_VERSION` | `1.10.2` | Apache Kyuubi |
| `JUPYTER_VERSION` | `4.3.0` | JupyterLab (`jupyterlab==` в образе) |
| `AIRFLOW_VERSION` | `2.6.3` | Apache Airflow (образ `apache/airflow:<version>-python3.10`) |
| `JAVA_VERSION` | `8` | Java (OpenJDK) |

#### OpenLineage
| Переменная | Значение | Описание |
|------------|----------|----------|
| `OPENLINEAGE_VERSION` | `1.46.0` | Версия OpenLineage |
| `OPENLINEAGE_CONFIG_RESEED` | `false` | `true` — при следующем старте контейнера перезаписать Variable `openlineage_config` дефолтным JSON (см. ниже). Сидинг иначе идемпотентный: существующую Variable не трогает, иначе правка через UI не пережила бы перезапуск |

Переменных `OPENLINEAGE_NAMESPACE`, `OPENLINEAGE_URL` и `OPENLINEAGE_JAR` в `.env` больше нет: адрес
Marquez, namespace, jar openlineage-spark и общий выключатель лайниджа целиком переехали в Airflow
Variable `openlineage_config` (формат — ниже) и правятся в UI (**Admin → Variables**) — правка
действует со **следующего запуска таски**, без рестарта и пересборки контейнера.

OL-листенер **не** включён глобально в общий `spark-defaults.conf` — иначе он навешивался бы и на
интерактивный `spark-shell` и ломал его. Вместо этого OL инжектится **точечно, на стороне каждого
рантайма**, который должен писать лайнидж:
- **Airflow** — cluster policy `task_policy` (`airflow/config/airflow_local_settings.py`, точка
  входа, которую Airflow ищет по имени файла) без собственной логики делегирует всё пакету
  `airflow/config/ol_policy/`; тот на парсе DAG-файла навешивает `on_execute_callback` на каждый
  `SparkSubmitOperator`, а на воркере (до `execute()`) колбэк читает Variable, зондирует HDFS и
  пишет OL-конфиг в атрибут `conf` таски (без правок в DAG'ах). Джобы идут в `deploy-mode=cluster`,
  `spark.yarn.jars` не задан → spark-submit заливает клиентский `$SPARK_HOME/jars` как classpath
  драйвера. Поэтому openlineage-spark jar **удалён из airflow-образа** (`airflow/Dockerfile`) и
  берётся **из HDFS**: колбэк дописывает jar-URI из поля `openlineage_jar` Variable в атрибут `jars`
  оператора (тот уезжает в `--jars`), а строковый ключ `conf["spark.jars"]`, если DAG его задал,
  забирается в тот же мердж и **удаляется из итогового conf** — иначе jar-список был бы объявлен
  дважды и полагался бы на приоритет `--jars` у spark-submit. Jar заливается в HDFS
  скриптом `scripts/seed-openlineage-jar.bat` (вызывается из `start-cluster.bat` автоматически).
  Наличие jar проверяется зондом WebHDFS по эндпоинтам из `HADOOP_CONF_DIR` **на воркере**, после
  рендера (не на парсе DAG-файла): нет jar — лайнидж не включается, чтобы джоба не упала с
  `ClassNotFoundException`;
- **Jupyter** — `PYSPARK_SUBMIT_ARGS` в `jupyter/scripts/start-jupyter.sh` (свой независимый конфиг
  только для Spark-сессий ноутбуков, `OPENLINEAGE_URL`/`OPENLINEAGE_NAMESPACE` этого файла эту
  Variable не используют и не читают);
- **Kyuubi** — `spark.*`-ключи в `kyuubi/config/kyuubi-defaults.conf` (пробрасываются в порождаемый engine).

Поэтому `spark-shell` и «голая» нода `hadoop`/history листенер не грузят.

#### Конфиг лайниджа Airflow: Variable `openlineage_config`

Адрес Marquez, namespace, jar и общий выключатель лайниджа живут в Airflow Variable
`openlineage_config`, а не в окружении. Правится в UI (**Admin → Variables**), подхватывается
**со следующего запуска таски**, без рестарта и пересборки. Значение — JSON-объект с тремя ключами:
`enabled` (bool), `spark_conf` (object) и `openlineage_jar` (строка, HDFS-URI со схемой). Пример —
ровно то, чем контейнер сидирует Variable при первом старте (`airflow/scripts/start-airflow.sh`):

```json
{
    "enabled": true,
    "spark_conf": {
        "spark.extraListeners": "io.openlineage.spark.agent.OpenLineageSparkListener",
        "spark.openlineage.transport.type": "http",
        "spark.openlineage.transport.url": "http://marquez:5000",
        "spark.openlineage.namespace": "hadoop-cluster",
        "spark.openlineage.columnLineage.datasetLineageEnabled": "true"
    },
    "openlineage_jar": "hdfs://namenode:9000/opt/openlineage/openlineage-spark_2.13-1.46.0.jar"
}
```

- Из `spark_conf` политика читает ровно **три** ключа: `spark.extraListeners`,
  `spark.openlineage.transport.url` и `spark.openlineage.namespace`. Остальные ключи объекта
  (`spark.openlineage.transport.type`, `spark.openlineage.columnLineage.datasetLineageEnabled`) она
  не читает вовсе — те же два значения (`transport.type=http`,
  `columnLineage.datasetLineageEnabled=true`) политика прописывает в conf таски сама; держать их в
  `spark_conf` можно для полноты картины, на инъекцию это не влияет. Никакие другие ключи `spark_conf`
  в conf таски не попадают.
- Полное имя класса listener'а (`io.openlineage.spark.agent.OpenLineageSparkListener`) нигде не
  зашито в код политики — оно живёт только в значении Variable (пример выше — из
  `start-airflow.sh`) и в этом README.
- Переменная сидится при первом старте контейнера значением, показанным выше. Дальше правка `.env`
  на неё не влияет: источник конфигурации — Variable. Перезасеять дефолтами:
  `OPENLINEAGE_CONFIG_RESEED=true` в `.env` + перезапуск.
- Лайнидж включается, только если `enabled: true` **и** все три поля `spark_conf` из списка выше
  заполнены **и** `openlineage_jar` — непустой URI со схемой, файл которого фактически лежит в HDFS
  (зонд WebHDFS на воркере, в колбэке). Неполный или битый конфиг = «лайниджа нет» плюс предупреждение
  в логе таски; молча выключается ровно один случай — честный `enabled: false` (или форс-выключение из
  DAG'а, см. ниже).
- Ключ `auth` в Variable **не поддерживается**: любое значение из conf уезжает в командную строку
  `spark-submit` и видно в `ps` и в YARN.

#### Мердж DAG-conf и Variable

Таска сама вправе задать `spark.extraListeners`, `jars`/`conf["spark.jars"]`,
`spark.openlineage.transport.url` и `spark.openlineage.namespace` — политика не затирает их молча:

- **`spark.extraListeners`** — **мердж**: сначала листенеры, которые перечислил DAG, затем
  OL-listener из Variable; дубликаты убираются, порядок сохраняется.
- **jar'ы** — тоже **мердж**: атрибут `jars` и строковый `conf["spark.jars"]` таски складываются с
  `openlineage_jar` из Variable в одну CSV-строку без дублей; итог пишется **в атрибут `jars`
  оператора** (`--jars` при сабмите), а сам ключ `spark.jars` из итогового conf **удаляется** —
  его элементы уже уехали в `--jars`, двойное объявление полагалось бы на приоритет `--jars`.
- **`spark.openlineage.transport.url`** и **`spark.openlineage.namespace`** — здесь **побеждает
  OpenLineage**: значение из Variable подставляется целиком, DAG-значение того же ключа в результат
  не входит (только упоминается в логе как перебитое).
- Отказ от лайниджа (форс-выключение, `enabled: false`, неполный конфиг Variable или отсутствующий
  в HDFS jar) не стирает то, что DAG сам положил в `jars`/`conf` — политика возвращает собственное
  значение DAG'а, а не пустую строку.
- Технически это двухфазный процесс. На **парсе** DAG-файла политика видит исходные DAG-значения и
  идемпотентно дописывает колбэк в `on_execute_callback` таски; Variable и HDFS на парсе не читаются
  (иначе сеть и метастор в этой точке жгли бы бюджет `[core] dag_file_processor_timeout` шедулера на
  каждый цикл разбора DAG-bag'а). На **воркере**, когда Airflow запускает колбэк перед `execute()`,
  значения (адрес, namespace, jar) читаются из Variable, зондируется HDFS, и результаты пишутся в
  атрибут `conf` таски. Рендер Jinja происходит до колбэка, поэтому переменные таски уже отрендерены.

#### Тумблер лайниджа в DAG'е

Ключ `openlineage` в `params` форсирует решение поверх Variable — на уровне таски или всего DAG'а:

```python
with DAG(dag_id="spark_etl_dag", params={"openlineage": False}, ...):      # весь DAG без лайниджа
    SparkSubmitOperator(task_id="aggregate", params={"openlineage": True}, ...)  # а эта таска — с ним
```

- Статический форс (`params={"openlineage": ...}` в коде DAG'а) решается **на парсе DAG-файла**:
  форс-выключение останавливает политику до дозаписи колбэка, и никакая правка на запуске это уже
  не изменит. Нейтральный ключ (см. ниже) колбэк не блокирует, а Airflow мерджит `conf` из формы
  «Trigger DAG w/ config» в `task.params` перед вызовом колбэка (`[core]
  dag_run_conf_overrides_params`, включено по умолчанию) — поэтому в этом случае тумблер из формы
  запуска колбэк увидит и учтёт наравне со статическим форсом.
- **Объявление ключа — это уже решение, а не подпись к нему.** `Param(True/False, ...)` резолвится в
  свой дефолт и работает как постоянный форс. Нейтральных вариантов два: не объявлять ключ вовсе
  (обычный случай, так сделано в обоих DAG'ах стенда) либо
  `Param(None, type=["null", "boolean"], description=...)` — `None` нейтрален и предупреждений не пишет.
- Форс-включение **не обходит** ни зонд jar, ни проверку полноты конфига: `True` при недоступном jar
  или негодном `url` лайнидж не включит, но напишет предупреждение с `dag_id` и `task_id`.
- Форс-выключение — единственный случай, когда политика молчит: объяснять там нечего.

#### Известные ограничения OpenLineage инъекции Airflow

- `airflow tasks run --read-from-db` (Airflow 2.10+) берёт таску из сериализованного DAG'а в БД:
  `on_execute_callback` там хранится как исходный текст функции (`get_python_source`), а при
  десериализации `SerializedBaseOperator` не воссоздаёт из него вызываемый объект — Airflow пытается
  вызвать получившуюся строку и на каждом запуске таски пишет в её лог `TypeError`
  (`Failed when executing execute callback`). Лайнидж в этом случае не включается, но ошибка не
  глушится молча. Используйте CLI без этого флага или полноценный запуск DAG'а.
- **Rendered Templates** в UI (Admin → DAG → Task → Rendered Templates) не показывает OL-ключи
  (`spark.extraListeners`, `spark.openlineage.transport.url`, `spark.openlineage.namespace`),
  потому что инъекция происходит в колбэке после сохранения Rendered Template Instances в БД.
  Итоговые значения можно видеть в логе задачи.
- Кэша у политики нет: Airflow форкает свежий процесс под каждую `TaskInstance`, поэтому Variable
  читается и jar зондируется один раз на запуск таски. Обратная сторона: при недоступном WebHDFS
  каждая таска платит полный двухпроходный зонд (до `_PROBE_DEADLINE_SEC`, 17 с) перед `execute()`.

Юнит-тесты политики лежат рядом с ней (`airflow/config/tests`) и гоняются
`tests\test-policy.bat`.

## Подключения

### Hive через DBeaver / JDBC
- **Драйвер**: Apache Hive 3.1+
- **Host**: `localhost`
- **Port**: `10000`
- **User**: `hadoop`
- **Password**: (пусто)
- **Database**: `default`
- **JDBC URL**: `jdbc:hive2://localhost:10000/default`

> Hive использует **Tez** в качестве движка выполнения запросов, что значительно быстрее MapReduce. DAG-задачи можно мониторить в [Tez UI](http://localhost:9999).

### Kyuubi через DBeaver / JDBC

> Требует профиль compose `kyuubi` (см. "Опциональные сервисы" выше).

- **Драйвер**: Apache Hive 3.1+
- **Host**: `localhost`
- **Port**: `10009`
- **User**: `hadoop`
- **Password**: (пусто)
- **Database**: `default`
- **JDBC URL**: `jdbc:hive2://localhost:10009/default`

> Kyuubi использует **Spark SQL** в качестве движка, поддерживает все возможности Spark SQL.

### Spark через Jupyter

> Требует профиль compose `jupyter` (см. "Опциональные сервисы" выше).

- Откройте http://localhost:8888
- Доступны ядра: Python (PySpark), Scala (Toree)
- Автоматически подключён к YARN

### Airflow

Оркестратор для запуска Spark-джоб на YARN. UI: http://localhost:8080, учётка по умолчанию `admin` / `admin`
(переопределяется `AIRFLOW_ADMIN_USER` / `AIRFLOW_ADMIN_PASSWORD`).

> ⚠️ **Креды инициализации видны через `docker inspect`, пока жив контейнер.** После слияния трёх
> контейнеров Airflow в один креды суперпользователя Postgres (`AIRFLOW_DB_ADMIN_USER` /
> `AIRFLOW_DB_ADMIN_PASSWORD`) и пароль администратора UI (`AIRFLOW_ADMIN_PASSWORD`) заданы в
> `docker-compose.yml` как обычные переменные окружения сервиса `airflow` — `docker inspect hadoop-airflow`
> (или любой доступ к хосту/сокету Docker) показывает их в открытом виде всё время жизни контейнера,
> это не лечится изнутри контейнера. `start-airflow.sh` после разовой инициализации делает `unset` этих
> переменных перед запуском scheduler'а и webserver'а — это закрывает только вторую дыру: без `unset`
> любой DAG читал бы их через `os.environ`, так как таски исполняются как дочерние процессы того же
> долгоживущего процесса. Для реального окружения (не локального стенда) такая схема хранения кредов
> неприемлема в любом случае.

- Версия задаётся `AIRFLOW_VERSION` в `.env` (по умолчанию `2.6.3`).
- DAG'и и джобы лежат в `airflow/dags` и `airflow/jobs`, смонтированы в контейнеры — правка не требует пересборки образа.
- `spark_pi_dag` — smoke-проверка связки Airflow → spark-submit → YARN.
- `spark_etl_dag` — генерация и агрегация parquet в HDFS; лайнидж уезжает в Marquez. Джобы регистрируются
  в job-неймспейсе `hadoop-cluster`, а сами датасеты (raw.parquet, agg.parquet) — в неймспейсе URI
  хранилища `hdfs://namenode:9000` (это неймспейс, в котором их искать через `GET /api/v1/namespaces/...`).
- Метаданные Airflow живут в общем контейнере `hadoop-postgres` (база `airflow`), её создаёт сервис `airflow`
  (контейнер `hadoop-airflow`) при каждом старте, дождавшись healthcheck'а Postgres — инициализация выполняется
  перед запуском scheduler и webserver в этом же контейнере. Имя роли, пароль и базу можно переопределить в `.env`
  (`AIRFLOW_DB_USER`, `AIRFLOW_DB_PASSWORD`, `AIRFLOW_DB_NAME`; все они перечислены закомментированными
  в `env_example`) — из них же собирается строка подключения Airflow.
  Пароль подставляется в URI `postgresql+psycopg2://user:pass@host:port/db` как есть, поэтому символы `@ : / # ?`
  в нём использовать нельзя. Смена `AIRFLOW_DB_PASSWORD` на уже инициализированном стенде подхватывается:
  инициализация при каждом запуске приводит пароль роли к текущему значению.
- Учётка UI задаётся `AIRFLOW_ADMIN_USER` / `AIRFLOW_ADMIN_PASSWORD` и создаётся **один раз**, при первичной
  инициализации; позже пароль меняется только через UI или `airflow users delete` + пересоздание.
- Шифрование секретов в базе метаданных по умолчанию выключено (`AIRFLOW__CORE__FERNET_KEY` пуст) — пароли
  и `extra` коннекшенов, значения Variable лежат в Postgres открытым текстом. Для реальных кредов задайте
  `AIRFLOW_FERNET_KEY` в `.env` (`python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`).
- Коннекшен `spark_yarn` задаётся переменной окружения `AIRFLOW_CONN_SPARK_YARN` в `docker-compose.yml`
  (`spark://yarn?deploy-mode=cluster&spark-binary=spark-submit`). Такие коннекшены Airflow резолвит раньше базы
  метаданных и в UI (Admin → Connections) не показывает: одноимённый коннекшен, созданный через UI, ни на что не
  повлияет — менять параметры подключения к YARN нужно в compose.
- Джобы отправляются в `deploy-mode=cluster`: `spark-defaults.conf` указывает на интерпретатор `/opt/python/bin/python3`, которого в образе Airflow нет, поэтому драйвер должен уезжать в YARN.
- Джобы в образ не копируются — они приезжают только маунтами `./airflow/jobs` и `./spark/scripts/pyspark_pi.py`
  (последний монтируется поверх каталога), поэтому источник правды у них один: рабочая копия репозитория.
- Образ большой (~2.5 ГБ): в него копируются дистрибутивы Spark и Hadoop.

## Хранилище данных

### HDFS директории
| Путь | Назначение |
|------|------------|
| `/user/hive/warehouse` | Hive Warehouse |
| `/tmp/hive` | Hive Scratch |
| `/apps/tez/tez.tar.gz` | Библиотеки Tez |
| `/tmp/tez/staging` | Tez staging |
| `/spark-events` | Spark Event Logs |
| `/tmp` | Временные файлы |

### Создание директорий вручную

```bash
docker exec hadoop-node hdfs dfs -mkdir -p /user/hive/warehouse /tmp/hive /tmp /spark-events
docker exec hadoop-node hdfs dfs -chmod 1777 /tmp
docker exec hadoop-node hdfs dfs -chmod 1777 /user/hive/warehouse
docker exec hadoop-node hdfs dfs -chmod 733 /tmp/hive
```

## Тестирование

### Быстрая проверка

```bash
# Тестирование всех компонентов
tests\test-cluster.bat
```

### Пошаговое тестирование

| Тест | Команда | Что проверяет |
|------|---------|---------------|
| HDFS | `tests\test-hdfs.bat` | NameNode, DataNode, создание файлов, репликация |
| YARN | `tests\test-yarn.bat` | ResourceManager, NodeManager, MapReduce |
| Spark | `tests\test-spark.bat` | Spark Pi на YARN, PySpark, History Server |
| Hive | `tests\test-hive.bat` | HiveServer2, создание таблиц, SQL-запросы, Metastore |
| Kyuubi | `tests\test-kyuubi.bat` | Beeline, Spark SQL таблицы, приложения в YARN (нужен профиль `kyuubi`, см. "Опциональные сервисы") |
| OpenLineage | `tests\test-openlineage.bat` | Marquez API/Web, guard отсутствия OL-листенера в общем `spark-defaults.conf`, чистый прямой submit |
| Airflow | `tests\test-airflow.bat` | Health контейнеров, импорт DAG'ов, прогон обоих DAG'ов, артефакты в HDFS и лайнидж, инъекция OL и тумблер в собранной команде |
| Cluster policy | `tests\test-policy.bat` | Юнит-тесты `airflow/config/tests` внутри контейнера: тумблер, обе раскладки атрибутов провайдера, зонд jar, разбор конфигов кластера |

### Живые e2e-тесты (`tests/live`)

```bash
set OL_LIVE_E2E=true
.venv\Scripts\python.exe -m pytest tests/live
```

Гоняются хостовым интерпретатором против уже поднятого стенда (`airflow`, `marquez`):
прогоняют DAG'и через `docker exec` и проверяют лайнидж через REST API Marquez.
Полный прогон нужно начинать со свежесброшенного стенда. Скипается целиком, если Marquez или
контейнер Airflow недоступны.

Набор требует явного подтверждения переменной окружения `OL_LIVE_E2E=true` — без неё весь модуль
скипается ещё до готовностных проверок. Это защита от случайного попадания в разрушительный прогон:
последний тест набора необратимо отравляет Marquez, а без пина `rootdir` бэйр `pytest`, запущенный
из родительского каталога `SparkAPI`, собрал бы этот набор как часть своего обычного полного прогона.

Набор не различает варианты образа сам — `tests/live/conftest.py` просто ходит в уже поднятый
контейнер `hadoop-airflow` и Marquez, какая бы версия Airflow там ни крутилась. Прогнать оба
варианта — значит прогнать набор дважды, переключив образ между прогонами:

```bash
set OL_LIVE_E2E=true

# Вариант base (Airflow 2.6.3) — образ, который поднимает start-cluster.bat по умолчанию
.venv\Scripts\python.exe -m pytest tests/live

# Сборка и переключение на вариант cloud (Airflow 2.10.2)
docker compose --profile build build airflow-image-cloud
AIRFLOW_IMAGE=hadoop-cluster-airflow:2.10.2 docker compose up -d airflow
.venv\Scripts\python.exe -m pytest tests/live
```

Схема метаданных Airflow при переключении `base → cloud` **мигрирует вперёд** сама
(`start-airflow.sh` выбирает `db migrate`/`db init` по версии образа). Обратного пути нет:
**Airflow не поддерживает даунгрейд схемы** — переключение `cloud → base` на том же томе
метаданных падает на alembic (не может разрешить более новую ревизию назад). Чтобы вернуть стенд
на базовый образ, том нужно сбросить: `docker compose down -v` перед следующим `start-cluster.bat`.

> Полный прогон `tests/live` оставляет Marquez с отравленным namespace'ом и вечным
> `500` на `GET /api/v1/namespaces`. Это негативный контроль в
> `tests/live/test_resolver_e2e.py` делает свою работу — намеренно шлёт в Marquez
> namespace с запятой, чтобы доказать, что без резолвера multi-host JDBC namespace
> не нормализуется, — а не поломка стенда. Перед следующим полным прогоном стенд
> нужно сбросить: `docker compose down -v`.
>
> Набор сам это проверяет: если Marquez поднят, но уже отравлен предыдущим
> прогоном, `tests/live` не скипается и не гоняет DAG'и, а сразу падает с явной
> ошибкой, требующей `docker compose down -v`. Скип остаётся только для случая
> "стенд вообще не поднят".

## Ручное управление

### Публикация образов в Docker Hub

```bash
# Вычислить теги (dry-run)
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\push-images.ps1 -DryRun

# Tag + push всех образов (base, spark, hive, jupyter, kyuubi, airflow)
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\push-images.ps1
```

Теги вычисляются из `.env` единым скриптом `scripts/image-tags.ps1` и переиспользуются и в `scripts/push-images.ps1`, и в `start-cluster.bat`.

### Сборка образов

```bash
# Базовый образ (Hadoop + Python + Scala)
docker compose build base

# Spark образ
docker compose build spark-image

# Hive образ (включает Tez, Metastore + HiveServer2)
docker compose build hive

# Jupyter образ
docker compose build jupyter

# Kyuubi образ
docker compose build kyuubi

# Airflow образ (копирует /opt/spark и /opt/hadoop из уже собранных образов стенда)
docker compose build airflow-image
```

### Управление сервисами

```bash
# Запуск основных семи сервисов (без kyuubi и jupyter)
docker compose up -d

# Запуск вместе с опциональными сервисами
docker compose --profile kyuubi --profile jupyter up -d

# Остановка (см. предупреждение про профили в разделе "Быстрый старт")
docker compose down

# Перезапуск конкретного сервиса (явное имя активирует его профиль, если он есть)
docker compose restart hive
```

### Просмотр логов

```bash
# Все логи
docker compose logs -f

# Логи конкретного сервиса
docker compose logs -f hadoop
docker compose logs -f hive
docker compose logs -f webproxy
```

Статику TEZ UI публикует контейнер `hadoop-hive` в том `tez-ui-static`, а отдаёт
её nginx (`webproxy`) на порту 9999 — отдельного сервиса `tez-ui` больше нет.

## Мониторинг

### YARN приложения
- ResourceManager: http://localhost:8088
- Timeline Server: http://localhost:8188
- NodeManager: http://localhost:8042

### Tez DAG
- Tez UI: http://localhost:9999 — визуализация DAG, счётчики, диагностика

### Spark
- History Server: http://localhost:18080

### HDFS статус

```bash
docker exec hadoop-node hdfs dfsadmin -report
```

### Проверка сервисов

```bash
# HDFS
docker exec hadoop-node hdfs dfs -ls /

# YARN
docker exec hadoop-node yarn node -list

# Hive
docker exec hadoop-hive beeline -u 'jdbc:hive2://localhost:10000' -n hadoop -e 'SHOW DATABASES;'

# Kyuubi
docker exec hadoop-kyuubi beeline -u 'jdbc:hive2://localhost:10009' -n hadoop -e 'SHOW DATABASES;'
```

## Устранение неполадок

### Проблемы с портами
Убедитесь, что следующие порты не заняты:

| Порт | Сервис |
|------|--------|
| 3000 | Marquez Web |
| 5000 | Marquez API |
| 5433 | Общий PostgreSQL (совместимость с прежним marquez-db) |
| 5434 | Общий PostgreSQL (hive_metastore, airflow, marquez) |
| 8042 | YARN NodeManager UI |
| 8080 | Airflow Web UI |
| 8088 | YARN ResourceManager UI |
| 8188 | YARN Timeline Server |
| 8888 | JupyterLab |
| 9083 | Hive Metastore Thrift |
| 9864 | HDFS DataNode UI |
| 9870 | HDFS NameNode UI |
| 9999 | Tez UI |
| 10000 | HiveServer2 Thrift |
| 10002 | HiveServer2 Web UI |
| 10009 | Kyuubi Thrift |
| 18080 | Spark History Server |

### Проблемы с памятью
- Увеличьте память Docker Desktop (рекомендуется 8 GB+)
- Настройки ресурсов YARN: `base/config/yarn-site.xml` (по умолчанию 8192 MB / 4 vCores)

### Проблемы с сетью
- Проверьте Docker сеть: `docker network ls` (сеть `hadoopclusternet`)
- Пересоздайте сеть: `docker network prune`

### Пересборка после изменений

```bash
# Полная пересборка
docker compose down
docker compose build --no-cache base spark-image hive airflow-image
docker compose up -d
```

`base`, `spark-image` и `airflow-image` лежат в опциональном профиле `build` — `docker compose build`
без явного списка имён их пропускает и соберёт только `hive` (обычный сервис вне профилей). Поэтому
для полной пересборки сервисы нужно перечислять явно, как в примере выше.

Если нужно принудительно игнорировать pull и пересобрать только локально, запустите `start-cluster.bat --build` — скрипт соберёт все образы сам, без обращения к Docker Hub.

## Полезные ссылки

- [Apache Hadoop](https://hadoop.apache.org/)
- [Apache Spark](https://spark.apache.org/)
- [Apache Hive](https://hive.apache.org/)
- [Apache Tez](https://tez.apache.org/)
- [Apache Kyuubi](https://kyuubi.apache.org/)
- [OpenLineage](https://openlineage.io/)
- [JupyterLab](https://jupyterlab.readthedocs.io/)
