# Hadoop Cluster для тестирования

Тестовый кластер Hadoop с полным стеком технологий для разработки и тестирования Big Data приложений.

## Компоненты кластера

- **Hadoop 3.3.6** — HDFS, YARN, MapReduce, Timeline Server
- **Hive 3.1.3** — Data Warehouse с PostgreSQL 13 и движком **Apache Tez**
- **Apache Tez 0.10.2** — DAG-движок для Hive (замена MapReduce), с Tez UI
- **Spark 3.5.2** — Обработка данных и машинное обучение
- **JupyterLab** — Интерактивная разработка с PySpark и Scala
- **Kyuubi 1.10.2** — Spark SQL через JDBC/Thrift
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
# Полный запуск: pull из Docker Hub, при отсутствии тега — build, затем запуск + health-check
start-cluster.bat

# Полный запуск с очисткой volumes
start-cluster.bat clean

# Остановка
docker compose stop

# Остановка с удалением контейнеров
docker compose down
```

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
| JupyterLab | http://localhost:8888 | Интерактивная разработка |
| Airflow | http://localhost:8080 | Оркестрация DAG'ов (учётка по умолчанию `admin` / `admin`) |
| Marquez Web | http://localhost:3000 | Трассировка данных |
| Marquez API | http://localhost:5000 | API для трассировки |

## Архитектура

```
┌───────────────────────────────────────────────────────────────┐
│                 Nginx Reverse Proxy (webproxy)                │
│       :9870 :8088 :8188 :9864 :8042 :10002 :9999 :18080       │
└─────────┬───────────────┬──────────────┬──────────────────────┘
          │               │              │
┌───────────────────┐  ┌─────┐  ┌─────────────────┐
│ Hadoop Node       │  │ Tez │  │ Hive            │
│                   │  │  UI │  │                 │
│ - NameNode        │  └─────┘  │ - Metastore     │
│ - DataNode        │           │ - HiveServer2   │
│ - ResourceManager │           └─────────────────┘
│ - NodeManager     │
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
│   ├── scripts/             # start-hive, start-tez-ui
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
│   ├── scripts/             # init-airflow.sh, ensure_db.py
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
| `OPENLINEAGE_NAMESPACE` | `hadoop-cluster` | Пространство имён |

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
- **Драйвер**: Apache Hive 3.1+
- **Host**: `localhost`
- **Port**: `10009`
- **User**: `hadoop`
- **Password**: (пусто)
- **Database**: `default`
- **JDBC URL**: `jdbc:hive2://localhost:10009/default`

> Kyuubi использует **Spark SQL** в качестве движка, поддерживает все возможности Spark SQL.

### Spark через Jupyter
- Откройте http://localhost:8888
- Доступны ядра: Python (PySpark), Scala (Toree)
- Автоматически подключён к YARN

### Airflow

Оркестратор для запуска Spark-джоб на YARN. UI: http://localhost:8080, учётка по умолчанию `admin` / `admin`
(переопределяется `AIRFLOW_ADMIN_USER` / `AIRFLOW_ADMIN_PASSWORD`).

- Версия задаётся `AIRFLOW_VERSION` в `.env` (по умолчанию `2.6.3`).
- DAG'и и джобы лежат в `airflow/dags` и `airflow/jobs`, смонтированы в контейнеры — правка не требует пересборки образа.
- `spark_pi_dag` — smoke-проверка связки Airflow → spark-submit → YARN.
- `spark_etl_dag` — генерация и агрегация parquet в HDFS; лайнидж уезжает в Marquez. Джобы регистрируются
  в job-неймспейсе `hadoop-cluster`, а сами датасеты (raw.parquet, agg.parquet) — в неймспейсе URI
  хранилища `hdfs://namenode:9000` (это неймспейс, в котором их искать через `GET /api/v1/namespaces/...`).
- Метаданные Airflow живут в общем контейнере `hadoop-postgres` (база `airflow`), её создаёт сервис `airflow-init`,
  дождавшись healthcheck'а Postgres. Имя роли, пароль и базу можно переопределить в `.env`
  (`AIRFLOW_DB_USER`, `AIRFLOW_DB_PASSWORD`, `AIRFLOW_DB_NAME`; все они перечислены закомментированными
  в `env_example`) — из них же собирается строка подключения Airflow.
  Пароль подставляется в URI `postgresql+psycopg2://user:pass@host:port/db` как есть, поэтому символы `@ : / # ?`
  в нём использовать нельзя. Смена `AIRFLOW_DB_PASSWORD` на уже инициализированном стенде подхватывается:
  `airflow-init` при каждом запуске приводит пароль роли к текущему значению.
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
| Kyuubi | `tests\test-kyuubi.bat` | Beeline, Spark SQL таблицы, приложения в YARN |
| OpenLineage | `tests\test-openlineage.bat` | Marquez API, трассировка Spark, метаданные |
| Airflow | `tests\test-airflow.bat` | Health контейнеров, импорт DAG'ов, прогон обоих DAG'ов, артефакты в HDFS и лайнидж |

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
# Запуск всего кластера
docker compose up -d

# Остановка
docker compose down

# Перезапуск конкретного сервиса
docker compose restart jupyter
```

### Просмотр логов

```bash
# Все логи
docker compose logs -f

# Логи конкретного сервиса
docker compose logs -f hadoop
docker compose logs -f hive
docker compose logs -f tez-ui
```

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
docker compose build --no-cache
docker compose up -d
```

Если нужно принудительно игнорировать pull и пересобрать только локально, удалите локальные образы и запустите `start-cluster.bat` — скрипт автоматически пересоберет только отсутствующие.

## Полезные ссылки

- [Apache Hadoop](https://hadoop.apache.org/)
- [Apache Spark](https://spark.apache.org/)
- [Apache Hive](https://hive.apache.org/)
- [Apache Tez](https://tez.apache.org/)
- [Apache Kyuubi](https://kyuubi.apache.org/)
- [OpenLineage](https://openlineage.io/)
- [JupyterLab](https://jupyterlab.readthedocs.io/)
