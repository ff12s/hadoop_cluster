# Hadoop Cluster для тестирования

Тестовый кластер с компонентами:
- Hadoop 3.3.6 (HDFS, YARN)
- Hive 3.1.3 (Metastore на PostgreSQL 13, HiveServer2)
- Java 8 (OpenJDK), Python 3.8, Scala 2.13.8
 - Spark 3.5.2, JupyterLab

## План построения

### Этап 1: Базовый образ ✅
- [x] Java 1.8
- [x] Python 3.8
- [x] Scala 2.13
- [x] SSH настройка
- [x] Базовые директории

### Этап 2: Hadoop + HDFS 🔄
- [x] Hadoop Core (сборка)
- [x] HDFS NameNode (конфигурация)
- [x] HDFS DataNode (конфигурация)
- [x] Тестирование HDFS

### Этап 3: YARN
- [x] ResourceManager
- [x] NodeManager
- [x] Тестирование YARN

### Этап 4: Hive
- [x] Hive Metastore (PostgreSQL)
- [x] HiveServer2
- [x] Тестирование Hive

### Дальше (при необходимости)
- [x] Spark (Spark History Server, тест SparkPi и PySpark на YARN)
- [x] JupyterLab (PySpark на YARN, каталог `/notebooks`)
- [x] Kyuubi (Thrift Binary 10009, Spark SQL engine на YARN)

## Запуск

### Быстрый старт
```bash
# Чистый запуск с пересборкой образов
start-cluster.bat clean

# Тестирование
tests\test-cluster.bat

# Остановка
stop-cluster.bat
```

### Пошаговое тестирование
```bash
# Тестирование HDFS
tests\test-hdfs.bat

# Тестирование YARN
tests\test-yarn.bat

# Полное тестирование
tests\test-cluster.bat

# Тестирование Spark отдельно
tests\test-spark.bat
```

### Ручное управление
```bash
# Сборка базового образа
docker build -t hadoop-cluster-base:latest ./base

# Сборка Hadoop образов
docker-compose build namenode datanode

# Запуск сервисов
docker-compose up -d

# Остановка
docker-compose down
```

## Структура проекта
```
hadoop_cluster/
├── base/                    # Базовый образ Hadoop (HDFS + YARN)
│   ├── Dockerfile
│   ├── config/              # Конфиги Hadoop: core-site, hdfs-site, yarn-site, mapred-site, hadoop-env
│   └── scripts/             # Скрипты запуска NN/DN/SSH
├── hive/                    # Образы Hive (metastore, hiveserver2)
│   ├── Dockerfile
│   ├── config/              # hive-site.xml
│   └── scripts/             # start-metastore.sh, start-hiveserver2.sh
├── tests/                   # Тестовые скрипты
│   ├── test-hdfs.bat        # Тест HDFS
│   ├── test-yarn.bat        # Тест YARN
│   ├── test-cluster.bat     # Полный тест
│   └── README.md
├── data/                    # Данные
├── logs/                    # Логи
├── start-cluster.bat        # Запуск кластера
├── stop-cluster.bat         # Остановка кластера
├── docker-compose.yml
├── env_file
└── README.md
```

## Переменные окружения
Все версии компонентов настраиваются в файле `env_file`.

## Веб-интерфейсы
- HDFS NameNode: `http://localhost:9870`
- YARN ResourceManager: `http://localhost:8088`
- HiveServer2 Web UI: `http://localhost:10002`
- Hive Metastore (Thrift): `tcp://localhost:9083`
 - Spark History Server: `http://localhost:18080`
 - JupyterLab: `http://localhost:8888`
 - Kyuubi (Thrift Binary): `tcp://localhost:10009`

## Подключение к Hive из DBeaver
Проверено подключение через DBeaver [[memory:5342975]].
- Драйвер: Apache Hive 3.1+
- Host: `localhost`, Port: `10000`
- User: `hadoop`, Password: пусто
- Database: `default` или `test_db`
- JDBC URL (пример): `jdbc:hive2://localhost:10000/default`

## Настройки Hive и HDFS
- Текущая директория складирования (warehouse) — в HDFS по пути `/opt/hive/warehouse`.
- Scratch-dir — в HDFS по пути `/opt/hive/tmp`.
- Значения задаются параметрами при старте `hiveserver2` и могут быть изменены в `hive/scripts/start-hiveserver2.sh`.

При необходимости ручной подготовки HDFS директорий:
```bash
docker exec hadoop-namenode hdfs dfs -mkdir -p /opt/hive/warehouse /opt/hive/tmp /tmp /spark-events
docker exec hadoop-namenode hdfs dfs -chmod 1777 /tmp
docker exec hadoop-namenode hdfs dfs -chmod 1777 /opt/hive/warehouse
docker exec hadoop-namenode hdfs dfs -chmod 733  /opt/hive/tmp
```

## Тестирование Spark
```bash
tests\test-spark.bat
```

## Kyuubi (Spark SQL over JDBC/Thrift)
```bash
tests\test-kyuubi.bat
```
- Ручное подключение через Beeline:
```bash
docker exec -it hadoop-hiveserver2 beeline -u 'jdbc:hive2://kyuubi:10009' -n hadoop
```
- Что делает тест:
  - Проверяет порт 10009 и процесс Kyuubi
  - Подключается beeline, выполняет DDL/DML в `kyuubi_db.kyuubi_table`
  - Проверяет появление данных в HDFS и приложений SPARK в YARN

### Конфигурация Kyuubi
- Тип движка: `kyuubi.engine.type=SPARK_SQL` в `kyuubi/config/kyuubi-defaults.conf`
- Spark/YARN: event logging в HDFS (`/spark-events`), warehouse в HDFS, master `yarn`, deploy-mode `client`
- Имперсонация (proxyuser): в `base/config/core-site.xml`:
  - `hadoop.proxyuser.hadoop.hosts=*`
  - `hadoop.proxyuser.hadoop.groups=*`
  (разрешает серверу Kyuubi запускать движки от имени `hadoop`).

## JupyterLab
- Том с ноутбуками монтируется в `/notebooks` (как оговорено [[memory:4954373]]).
- Запуск JupyterLab: контейнер `jupyter` стартует вместе с кластером командой `pyspark --master yarn` и поднимает UI на `http://localhost:8888` (без токена/пароля в локальной среде).
- Пример ноутбука `Welcome.ipynb` создаётся автоматически при пустом каталоге `/notebooks`.
- Примеры ноутбуков:
  - `notebooks/PySparkPi.ipynb` — PySpark Pi на YARN (ядро Python)
  - `notebooks/ScalaSparkPi.ipynb` — Spark Pi на YARN (ядро "Toree - Scala")

Пересборка Jupyter после изменений:
```bash
docker-compose build jupyter && docker-compose up -d jupyter
```

Если при `docker-compose` видите предупреждение вида "The \"SPARK_VERSION\" variable is not set":
- Добавьте переменную в `.env` или подключите `env_file` в compose, либо экспортируйте `SPARK_VERSION` в окружение перед запуском.

В тесте выполняются:
- Spark Pi (Scala) на YARN — через `spark-examples` jar
- PySpark Pi на YARN — `spark/scripts/pyspark_pi.py`

Замечания:
- История событий Spark пишется в HDFS по пути `/spark-events` и доступна в UI `http://localhost:18080`.
- При первом запуске Spark сам загрузит необходимые библиотеки в HDFS (`.sparkStaging`).
