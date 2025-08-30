# Hadoop Cluster для тестирования

Тестовый кластер Hadoop с полным стеком технологий для разработки и тестирования Big Data приложений.

## 🚀 Компоненты кластера

- **Hadoop 3.3.6** - HDFS, YARN, MapReduce
- **Hive 3.1.3** - Data Warehouse с PostgreSQL 13
- **Spark 3.5.2** - Обработка данных и машинное обучение
- **JupyterLab** - Интерактивная разработка с PySpark и Scala
- **Kyuubi 1.10.2** - Spark SQL через JDBC/Thrift
- **OpenLineage** - Трассировка данных (Marquez)
- **Java 8**, **Python 3.12**, **Scala 2.13.8**

## 📋 Быстрый старт

### Запуск кластера
```bash
# Полный запуск с пересборкой образов
start-cluster.bat

# Остановка
docker compose stop
```

## 🌐 Веб-интерфейсы

| Сервис | URL | Описание |
|--------|-----|----------|
| HDFS NameNode | http://localhost:9870 | Управление файловой системой |
| YARN ResourceManager | http://localhost:8088 | Управление ресурсами |
| Spark History Server | http://localhost:18080 | История Spark приложений |
| JupyterLab | http://localhost:8888 | Интерактивная разработка |
| HiveServer2 Web UI | http://localhost:10002 | Веб-интерфейс Hive |
| Marquez Web | http://localhost:3000 | Трассировка данных |
| Marquez API | http://localhost:5000 | API для трассировки |

## 📁 Структура проекта

```
hadoop_cluster/
├── base/                    # Базовый образ Hadoop
│   ├── config/              # Конфиги: core-site, hdfs-site, yarn-site, mapred-site
│   ├── scripts/             # Скрипты запуска и проверки
│   └── Dockerfile
├── hive/                    # Hive (Metastore + HiveServer2)
│   ├── config/              # hive-site.xml
│   ├── scripts/             # Скрипты запуска
│   └── Dockerfile
├── spark/                   # Spark с History Server
│   ├── config/              # spark-defaults.conf, log4j.properties
│   ├── scripts/             # Скрипты запуска и тестирования
│   └── Dockerfile
├── jupyter/                 # JupyterLab
│   ├── notebooks/           # Jupyter ноутбуки
│   ├── scripts/             # Скрипты запуска
│   └── Dockerfile
├── kyuubi/                  # Kyuubi (Spark SQL)
│   ├── config/              # kyuubi-defaults.conf
│   ├── scripts/             # Скрипты запуска
│   └── Dockerfile
├── marquez/                 # OpenLineage
│   └── config/              # config.yml
├── tests/                   # Тестовые скрипты
├── docker-compose.yml       # Конфигурация кластера
├── .env                     # Переменные окружения
└── README.md
```

## ⚙️ Конфигурация

### Переменные окружения (.env)

#### Версии компонентов
- `HADOOP_VERSION=3.3.6` - Apache Hadoop
- `HIVE_VERSION=3.1.3` - Apache Hive
- `SPARK_VERSION=3.5.2` - Apache Spark
- `SCALA_VERSION=2.13.8` - Scala
- `PYTHON_VERSION=3.12.7` - Python
- `KYUUBI_VERSION=1.10.2` - Apache Kyuubi
- `JUPYTER_VERSION=latest` - JupyterLab
- `JAVA_VERSION=8` - Java (OpenJDK)

#### OpenLineage
- `OPENLINEAGE_VERSION=1.37.0` - Версия OpenLineage
- `OPENLINEAGE_NAMESPACE=hadoop-cluster` - Пространство имен


## 🔌 Подключения

### Hive через DBeaver
- **Драйвер**: Apache Hive 3.1+
- **Host**: `localhost`
- **Port**: `10000`
- **User**: `hadoop`
- **Password**: (пусто)
- **Database**: `default`
- **JDBC URL**: `jdbc:hive2://localhost:10000/default`

> **Примечание**: Hive использует MapReduce движок, который медленнее Spark. Для лучшей производительности рекомендуется использовать **Kyuubi** (порт 10009), который работает на Spark SQL.

### Kyuubi через DBeaver
- **Драйвер**: Apache kyuubi Hive 3.1+
- **Host**: `localhost`
- **Port**: `10009`
- **User**: `hadoop`
- **Password**: (пусто)
- **Database**: `default`
- **JDBC URL**: `jdbc:hive2://localhost:10009/default`

> **Примечание**: Kyuubi использует Spark SQL движок, поэтому поддерживает все возможности Spark SQL.

### Spark через Jupyter
- Откройте http://localhost:8888
- Доступны ядра: Python (PySpark), Scala (Toree)
- Автоматически подключен к YARN

## 🗄️ Хранилище данных

### HDFS директории
- **Warehouse**: `/user/hive/warehouse`
- **Scratch**: `/tmp/hive`
- **Spark Events**: `/spark-events`
- **Temp**: `/tmp`

### Создание директорий
```bash
docker exec hadoop-namenode hdfs dfs -mkdir -p /user/hive/warehouse /tmp/hive /tmp /spark-events
docker exec hadoop-namenode hdfs dfs -chmod 1777 /tmp
docker exec hadoop-namenode hdfs dfs -chmod 1777 /user/hive/warehouse
docker exec hadoop-namenode hdfs dfs -chmod 733 /tmp/hive
```

## 🧪 Тестирование

### Быстрая проверка
```bash
# Тестирование всех компонентов
tests\test-cluster.bat
```

### Пошаговое тестирование

#### HDFS тест
- Доступность NameNode и DataNode
- Создание и удаление файлов
- Копирование данных между узлами
- Проверка репликации

```bash
# HDFS - файловая система
tests\test-hdfs.bat
```

#### YARN тест
- Запуск MapReduce приложения
- Проверка ResourceManager
- Мониторинг контейнеров
- Статус NodeManager

```bash
# YARN - управление ресурсами
tests\test-yarn.bat
```

#### Spark тест
- Запуск Spark Pi на YARN
- PySpark приложение
- Проверка Spark History Server
- Подключение к Hive

```bash
# Spark - обработка данных
tests\test-spark.bat
```
#### Hive тест
- Подключение к HiveServer2
- Создание базы данных и таблиц
- Выполнение SQL запросов
- Проверка Metastore

```bash
# Hive - хранилище данных
tests\test-hive.bat
```
#### Kyuubi тест
- Подключение через Beeline
- Создание таблиц через Spark SQL
- Вставка и выборка данных
- Проверка приложений в YARN

```bash
# Kyuubi - Spark SQL через JDBC
tests\test-kyuubi.bat
```
#### OpenLineage тест
- Проверка Marquez API
- Трассировка Spark приложений
- Сбор метаданных
- Визуализация в Marquez Web

```bash
# OpenLineage - трассировка данных
tests\test-openlineage.bat
```


## 🔧 Ручное управление

### Сборка образов
```bash
# Базовый образ
docker-compose build base

# Spark образ
docker-compose build spark-image

# Jupyter образ
docker-compose build jupyter
```

### Управление сервисами
```bash
# Запуск
docker-compose up -d

# Остановка
docker-compose down

# Перезапуск конкретного сервиса
docker-compose restart jupyter
```

### Просмотр логов
```bash
# Все логи
docker-compose logs -f

# Логи конкретного сервиса
docker-compose logs -f namenode
```

## 📊 Мониторинг

### YARN приложения
- Web UI: http://localhost:8088
- История Spark: http://localhost:18080

### HDFS статус
```bash
docker exec hadoop-namenode hdfs dfsadmin -report
```

### Проверка сервисов
```bash
# HDFS
docker exec hadoop-namenode hdfs dfs -ls /

# YARN
docker exec hadoop-namenode yarn node -list

# Hive
docker exec hadoop-hiveserver2 beeline -u 'jdbc:hive2://localhost:10000' -n hadoop -e 'SHOW DATABASES;'
```

## 🚨 Устранение неполадок

### Проблемы с портами
- Убедитесь, что порты 9870, 8088, 8888, 10000 не заняты
- Проверьте firewall на Windows

### Проблемы с памятью
- Увеличьте память Docker Desktop (рекомендуется 8GB+)
- Проверьте настройки YARN в `base/config/yarn-site.xml`

### Проблемы с сетью
- Проверьте Docker сеть: `docker network ls`
- Пересоздайте сеть: `docker network prune`

### Пересборка после изменений
```bash
# Полная пересборка
docker-compose down
docker-compose build --no-cache
docker-compose up -d
```

## 🔗 Полезные ссылки

- [Apache Hadoop](https://hadoop.apache.org/)
- [Apache Spark](https://spark.apache.org/)
- [Apache Hive](https://hive.apache.org/)
- [Apache Kyuubi](https://kyuubi.apache.org/)
- [OpenLineage](https://openlineage.io/)
- [JupyterLab](https://jupyterlab.readthedocs.io/)
