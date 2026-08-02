# Тестовые скрипты кластера Hadoop

В этом каталоге лежат .bat-скрипты для управления стендом и его проверки.

## 📁 Файлы

### 🚀 Управление кластером
- **`../start-cluster.bat`** — запуск кластера с пересборкой образов (в корне репозитория)
- **`docker compose down`** — остановка кластера (см. предупреждение про профили в корневом `README.md`)

### 🧪 Тесты
- **`test-cluster.bat`** — полная проверка кластера
- **`test-hdfs.bat`** — только компоненты HDFS
- **`test-yarn.bat`** — только компоненты YARN
- **`test-airflow.bat`** — только оркестрация Airflow
- **`test-policy.bat`** — юнит-тесты cluster policy OpenLineage внутри контейнера Airflow (быстрые, кластер не нужен)

## 🎯 Использование

### Быстрый старт
1. Запустите `../start-cluster.bat`
2. Дождитесь завершения (около 30 секунд)
3. Запустите `test-cluster.bat` для проверки

### Пошаговая проверка
1. `test-hdfs.bat` — операции HDFS
2. `test-yarn.bat` — YARN и MapReduce
3. `test-policy.bat` — cluster policy OpenLineage (секунды, без прогона DAG'ов)
4. `test-airflow.bat` — DAG'и Airflow на YARN (прогоняет оба DAG'а, занимает несколько минут)
5. `test-cluster.bat` — комплексная проверка

### Остановка
- `docker compose down` — остановка; для очистки данных `docker compose down --volumes`

## 🌐 Веб-интерфейсы

Доступны после запуска кластера:

| Сервис | URL | Описание |
|--------|-----|----------|
| HDFS NameNode | http://localhost:9870 | Управление HDFS |
| YARN ResourceManager | http://localhost:8088 | Управление ресурсами |
| HDFS DataNode | http://localhost:9864 | Состояние DataNode |
| YARN NodeManager | http://localhost:8042 | Состояние NodeManager |
| Airflow | http://localhost:8080 | Оркестрация DAG'ов (учётка по умолчанию `admin` / `admin`) |

## 📊 Что проверяется

### Тесты HDFS:
- ✅ Состояние контейнеров
- ✅ Процессы NameNode и DataNode
- ✅ Состояние кластера HDFS
- ✅ Доступность веб-интерфейса
- ✅ Создание, чтение и запись файлов
- ✅ Проверка блоков HDFS

### Тесты YARN:
- ✅ Состояние ResourceManager и NodeManager
- ✅ Список узлов YARN
- ✅ Список приложений
- ✅ Доступность веб-интерфейса
- ✅ Запуск MapReduce-джобы (WordCount)
- ✅ Проверка ресурсов и очередей

### Тесты Airflow:
- ✅ Health единого контейнера `hadoop-airflow` (webserver и scheduler запущены в нём вместе)
- ✅ spark-submit / yarn / java в образе, непустые файлы джоб в `/opt/airflow/jobs` и ожидаемая сигнатура Spark-провайдера
- ✅ Отсутствие ошибок импорта DAG'ов и регистрация обоих DAG'ов
- ✅ Успешный прогон `spark_pi_dag` и `spark_etl_dag` (на время прогона DAG'и ставятся на паузу, состояния тасок проверяются явно)
- ✅ Приложение YARN именно этого прогона (id разбирается из лога таски) дошло до SUCCEEDED
- ✅ raw.parquet и agg.parquet записаны в HDFS
- ✅ Лайнидж доехал до Marquez, и `agg.parquet` обновлён этим прогоном (а не остался от предыдущего)
- ✅ Cluster policy: в фактически собранной команде `spark-submit` присутствует вызов макроса `__openlineage_v1`, и оба jar'а DAG'а (`mine.jar`, `other.jar`) сохранились в ней; `conf['spark.jars']` при этом не переписан
- ✅ Тумблер `params={"openlineage": False}` убирает листенер из команды
- ✅ Правка Variable `openlineage_config` подхватывается без рестарта, со следующего запуска таски (значение восстанавливается после проверки)

Мердж `spark.extraListeners` DAG'а с OL-listener'ом из Variable этот бат-скрипт не проверяет — команда
строится напрямую через `_build_spark_submit_command`, без рендера Jinja и без DAG-listener'а на
входе. Сам мердж (дедуп, порядок, побеждающий Variable) покрыт юнит-тестами
`airflow/config/tests/test_ol_policy.py` (запускаются `test-policy.bat`).

### Тесты cluster policy (`test-policy.bat`):
- ✅ Health контейнера `hadoop-airflow`
- ✅ Полный pytest-набор `/opt/airflow/config/tests` внутри контейнера: таблица истинности тумблера, обе раскладки атрибутов оператора (4.1.1 и 4.10.0), зонд jar с дедлайном, разбор конфигов кластера, инварианты политики
- ✅ Variable `openlineage_config` хранится как JSON-объект (регрессия на двойное кодирование через `variables set --json`)

### Общие тесты:
- ✅ Сетевая связность между контейнерами
- ✅ Интеграция HDFS и YARN
- ✅ Проверка логов
- ✅ Комплексная проверка работы

## ⚠️ Требования

- Docker Desktop для Windows
- Docker Compose
- curl (входит в Windows 10+)

## 🔧 Устранение неполадок

### Кластер не запускается:
1. Проверьте, что Docker Desktop запущен
2. Убедитесь, что порты 9870, 8088, 9864, 8042 свободны
3. Запустите `docker compose down --volumes` и повторите `../start-cluster.bat --clean`

### Тесты падают:
1. Дождитесь полного запуска сервисов (30+ секунд)
2. Посмотрите логи: `docker-compose logs`
3. Перезапустите кластер

## 📝 Логи

Логи контейнеров смотрятся так:
```bash
docker-compose logs hadoop
```
