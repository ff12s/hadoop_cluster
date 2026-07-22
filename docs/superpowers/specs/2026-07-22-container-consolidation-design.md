# Сплющивание контейнеров тест-стенда hadoop_cluster: 16 рантайм-сервисов → 9, из них 7 по умолчанию

**Дата:** 2026-07-22
**Статус:** утверждён (brainstorming), готов к плану реализации
**Артефакт:** `docker-compose.yml`, скрипты запуска сервисов, `nginx/nginx.conf`, `start-cluster.bat`, `tests/*.bat`
**Репозиторий:** `hadoop_cluster` (изменения только здесь)

## 1. Контекст и цель

Тест-стенд поднимает 16 рантайм-сервисов одним `docker-compose.yml` (плюс три сервиса с
`profiles: ["build"]`, которые не стартуют). Из них 15 долгоживущих и один одноразовый
(`airflow-init`). На разработческой машине это заметная нагрузка: два независимых инстанса
PostgreSQL, отдельный контейнер под статический файловый сервер, три контейнера Airflow, пара
контейнеров под демоны Hadoop, которые в этом стенде и так работают в псевдораспределённом режиме.

Цель — сократить число одновременно работающих контейнеров, **не меняя наблюдаемого поведения
стенда**: те же порты, те же DNS-имена внутри сети, те же тесты, тот же набор веб-интерфейсов.

**Не входит в скоуп:** тюнинг JVM-хипов, изменение числа gunicorn-воркеров Airflow (явно отклонено —
рефакторинг остаётся структурным), удаление `webproxy`, мега-мердж Hadoop+Hive в один контейнер,
профили для Marquez, переход на другую версию PostgreSQL, изменения в соседнем репозитории `SparkAPI`.

## 2. Грундинг-бриф — обязателен во всех брифах реализации

Пины прочитаны из `env_example` и `docker-compose.yml`. Полная версия — `GROUNDING.md` (см. §9).

| Факт | Источник |
| --- | --- |
| Файлы `/docker-entrypoint-initdb.d/*.{sh,sql,sql.gz,sql.xz,sql.zst}` выполняются **в алфавитном порядке** функцией `docker_process_init_files` | context7 `/docker-library/postgres`, query `"docker-entrypoint-initdb.d initialization scripts: when do they run, creating multiple databases and roles in one container"` |
| **Init-файлы выполняются только при пустом `PGDATA`.** `docker_setup_env` выставляет `DATABASE_ALREADY_EXISTS` по наличию `$PGDATA/PG_VERSION`; при выставленном флаге весь init-пайплайн пропускается. На существующем томе новый init-скрипт **не выполнится никогда** | context7 `/docker-library/postgres`, query `"initdb scripts best practices common pitfalls: existing PGDATA volume skips init, POSTGRES_DB single database limitation"` |
| `POSTGRES_DB` создаёт **ровно одну** начальную БД, «skipped if it already exists». Несколько БД — не фича переменных окружения | там же |
| `networks.<net>.aliases` объявляет дополнительные DNS-имена сервиса в сети; имена скоупятся сетью | context7 `/docker/docs`, query `"service networks aliases: give one container multiple DNS names on a network in compose file"` |
| `depends_on` длинный синтаксис: `condition` ∈ `service_started` / `service_healthy` / `service_completed_successfully`, плюс `restart: true` и `required: false` | context7 `/docker/docs`, query `"depends_on long syntax conditions service_healthy service_completed_successfully required restart"` |
| Сервисы **без** `profiles` включены всегда. Активация: `docker compose --profile <name> up` либо `COMPOSE_PROFILES`. Регексп имени профиля `[a-zA-Z0-9][a-zA-Z0-9_.-]+` | context7 `/docker/docs`, query `"compose profiles: assigning services to profiles, starting with --profile, and how depends_on interacts with profiles"` |
| Если у профильного сервиса есть зависимость в **другом**, невключённом профиле — модель невалидна. Зависимости обязаны быть в том же профиле, всегда включены, либо подняты отдельно | там же |
| **`docker compose down` не трогает контейнеры выключенных профилей**: «Running `docker compose down` only stops `backend` and `db`». Для остальных нужно `COMPOSE_PROFILES=... docker compose down` | context7 `/docker/docs`, query `"docker compose down and stop with profiles: are containers of disabled profiles removed, COMPOSE_PROFILES effect on down"` |
| Явное таргетирование профильного сервиса в CLI активирует его профиль автоматически (нужно для `docker compose build jupyter kyuubi`) | context7 `/docker/docs`, query `"compose profiles: assigning services to profiles..."` |
| `airflow db migrate` заменил `db init` / `db upgrade` **начиная с 2.7.0**. Репозиторий пинит **2.6.3** → `airflow db init` остаётся правильной командой; комментарий в `airflow/scripts/init-airflow.sh:8` это уже фиксирует. **Не «модернизировать»** | context7 `/apache/airflow/2_7_3`, query `"airflow standalone command running webserver and scheduler together, db init vs db migrate, production caveats"` (дельта: снапшота 2.6.x в context7 нет, использован ближайший 2.7.3) |
| Документированный продовый способ — отдельные команды `airflow webserver --port 8080` и `airflow scheduler`. `airflow standalone` документирован как development/quick-start (поднимает ещё и triggerer, сам создаёт админа) → не используем | там же |
| Миграцию схемы документация просит выполнять, когда компоненты Airflow не запущены → init-шаг строго до обоих процессов | там же |
| Блок `db:` в `marquez.yml` — обычный JDBC URL; внешний общий PostgreSQL — поддерживаемый сценарий (в Helm-чарте это `marquez.db.host/port/name/user/password`) | context7 `/marquezproject/marquez`, query `"configure database connection: MARQUEZ_DB_HOST MARQUEZ_DB_PORT MARQUEZ_DB MARQUEZ_USER MARQUEZ_PASSWORD and config.yml db url, using an existing external Postgres"` |
| `migrateOnStartup: true` → Marquez гоняет Flyway по своей БД на старте; БД и роль обязаны существовать раньше | там же |
| `README` Marquez на main заявляет требования «Java 17 и PostgreSQL 14». Стенд гоняет 0.47.0 на `postgres:13` сегодня → перенос на **другой** `postgres:13` версионно нейтрален | context7 `/marquezproject/marquez`, query `"Marquez PostgreSQL version requirements and database migration best practices common pitfalls"` |
| Embedded-метастор Hive работает **в одной JVM с HiveServer2** и выбирается пустым/незаданным `hive.metastore.uris`: «This mode is the default and will be used anytime the configuration parameter metastore.uris is not set» | [Hive AdminManual Metastore Administration](https://cwiki.apache.org/confluence/display/Hive/AdminManual+Metastore+Administration), [AdminManual Configuration](https://hive.apache.org/docs/latest/admin/adminmanual-configuration/) (2026-07-22) |
| Следствие: embedded **не отдаёт Thrift на 9083**, а по нему ходят Spark, Kyuubi и Jupyter (`hive/config/hive-site.xml`) → метастор обязан остаться отдельной JVM | там же + код репозитория |
| Псевдораспределённый режим Hadoop: каждый демон — отдельный Java-процесс на одной машине; NameNode и DataNode живут на одной машине, YARN добавляется запуском ResourceManager и NodeManager. Документированные настройки — `fs.defaultFS`, `dfs.replication=1` | [Hadoop 3.3.6 SingleCluster](https://hadoop.apache.org/docs/r3.3.6/hadoop-project-dist/hadoop-common/SingleCluster.html) (2026-07-22) |
| `base/config/hdfs-site.xml` уже задаёт `dfs.replication=1`; `start-namenode.sh` и `start-datanode.sh` уже запускают по несколько демонов в фоне — паттерн репозитория, а не новый | код репозитория |

## 3. Инвентаризация: все рассмотренные сплющивания

Пользователь просил рассмотреть все возможные варианты. Рассмотрены двенадцать; в скоуп вошли девять.

| # | Слияние | −контейнеров | Решение |
| --- | --- | --- | --- |
| P1 | `marquez-db` → `postgres` (отдельные БД и роли) | −1 | **В скоупе** |
| P2 | `namenode` + `datanode` → `hadoop` | −1 | **В скоупе** |
| P3 | `hive-metastore` + `hiveserver2` → `hive` | −1 | **В скоупе** |
| P4 | `tez-ui` → статика отдаётся nginx | −1 | **В скоупе** |
| P5 | `spark-history` → в `hadoop` | −1 | **В скоупе** |
| P6 | `airflow-webserver` + `airflow-scheduler` → `airflow` | −1 | **В скоупе** |
| P7 | `airflow-init` → entrypoint `airflow` | −1 | **В скоупе** |
| P9 | `kyuubi` → профиль `kyuubi`, opt-in | −1 по умолчанию | **В скоупе** |
| P10 | `jupyter` → профиль `jupyter`, opt-in | −1 по умолчанию | **В скоупе** |
| P8 | `marquez` + `marquez-web` → профиль `lineage` | −2 по умолчанию | Отклонено: lineage нужен постоянно |
| P11 | Убрать `webproxy`, публиковать порты напрямую | −1 | Отклонено: 188 строк nginx переписывают ссылки в Hadoop UI |
| P12 | Мега-мердж Hadoop + Hive + spark-history в один контейнер | −4 | Отклонено: теряется гранулярность рестарта и отладки |

Итог: **16 рантайм-определений сервисов → 9**, из них **7 стартуют по умолчанию**. Три сервиса
сборки с `profiles: ["build"]` в счёт не входят и не меняются.

## 4. Ключевой принцип: DNS сохраняем, `container_name` делаем честными

Полные развёртки по репозиторию (без обрезки вывода):

* **75 вхождений DNS-имён** (`namenode:9000`, `namenode:8188`, `datanode:`, `hive-metastore:9083`,
  `marquez-db`, `tez-ui`, `5433`) в **21 файле** — все `*-site.xml`, `spark-defaults.conf`,
  `kyuubi-defaults.conf`, `nginx.conf`, `marquez/config/config.yml`, ноутбуки.
* **128 вхождений старых `container_name`** в 15 файлах, из них 42 — в исторических
  `docs/superpowers/*` (историю не переписываем) → **86 правок в 13 живых файлах**, основная масса в
  `tests/*.bat`.

Отсюда решение:

* **DNS-имена не меняются вообще.** Каждый смёрженный сервис получает `networks.default.aliases` со
  всеми старыми именами. 75 вхождений в 21 файле остаются нетронутыми.
* **`container_name` меняются на честные.** Контейнер с именем `hadoop-namenode`, который на деле
  держит весь кластер, вводит в заблуждение. 86 правок механические, грепаются одним паттерном, и
  `tests/*.bat` служат проверкой полноты.

## 5. Целевая топология

| Сервис | `container_name` | Профиль | Процессы внутри | DNS aliases |
| --- | --- | --- | --- | --- |
| `hadoop` | `hadoop-node` | — | NameNode, DataNode, ResourceManager, NodeManager, TimelineServer, Spark History | `namenode`, `datanode`, `spark-history` |
| `postgres` | `hadoop-postgres` | — | PostgreSQL, БД `hive_metastore` / `airflow` / `marquez` | `marquez-db` |
| `hive` | `hadoop-hive` | — | Hive Metastore (:9083), HiveServer2 (:10000, :10002) | `hive-metastore`, `hiveserver2` |
| `airflow` | `hadoop-airflow` | — | init → scheduler + webserver | — |
| `marquez` | `hadoop-marquez` | — | без изменений | — |
| `marquez-web` | `hadoop-marquez-web` | — | без изменений | — |
| `webproxy` | `hadoop-webproxy` | — | nginx, включая статику TEZ UI на :9999 | `tez-ui` |
| `kyuubi` | `hadoop-kyuubi` | `kyuubi` | без изменений | — |
| `jupyter` | `hadoop-jupyter` | `jupyter` | без изменений | — |

Сервисы сборки (`base`, `spark-image`, `airflow-image`) с `profiles: ["build"]` остаются как есть.

Порты наружу не меняются ни одним пунктом: 5433, 5434, 9083, 10000, 10009, 8080, 8888, 3000, 5000 и
проксируемые nginx 9870, 8088, 8188, 9864, 8042, 10002, 9999, 18080.

### 5.1 `hadoop`

* Образ — **`${SPARK_IMAGE}`**, не `${BASE_IMAGE}`: spark-образ собран `FROM base` и является его
  надмножеством, иначе Spark History Server в этот контейнер не помещается.
* `user: "0:0"`; скрипт чинит права `timeline-data` и делает `runuser -u hadoop` — текущее поведение
  `start-namenode.sh`.
* Новый скрипт `base/scripts/start-hadoop.sh` последовательно поднимает: формат NameNode при первом
  запуске → `hdfs namenode &` → `yarn resourcemanager &` → `yarn timelineserver &` → `hdfs datanode &`
  → `yarn nodemanager &` → Spark History Server → `tail -f /dev/null`.
* Тома: `namenode-data`, `datanode-data`, `timeline-data` переезжают в этот сервис без переименования;
  `namenode-logs` и `datanode-logs` заменяются одним общим томом `hadoop-logs` — шесть демонов
  (NameNode, DataNode, ResourceManager, NodeManager, Timeline Server, Spark History) пишут файловые
  логи в него совместно, раздельных лог-томов на демон в реализации больше нет.
* Старые `base/scripts/start-namenode.sh` и `start-datanode.sh` удаляются.
  `spark/scripts/start-spark-history.sh` **остаётся файлом и вызывается** из нового скрипта — его
  содержимое не инлайнится, чтобы не дублировать логику запуска History Server.

### 5.2 `postgres`

* `POSTGRES_DB=hive_metastore`, `POSTGRES_USER=hive`, `POSTGRES_PASSWORD=hive` — без изменений.
* Новый файл `postgres/initdb/01-databases.sql`, монтируется в `/docker-entrypoint-initdb.d/`,
  создаёт роли и БД `airflow` и `marquez` с их паролями.
* Порты: **оба** — `5433:5432` и `5434:5432`, чтобы не сломать внешние подключения к бывшему
  `marquez-db`.
* Alias `marquez-db` оставляет `marquez/config/config.yml` (`jdbc:postgresql://marquez-db:5432/marquez`)
  и `MARQUEZ_DB_HOST` в compose без единой правки.
* Том `marquez-data` удаляется из `volumes:`.
* `airflow/scripts/ensure_db.py` сохраняется как идемпотентная подстраховка для роли Airflow.

### 5.3 `hive`

* Новый скрипт `hive/scripts/start-hive.sh`: ожидание PostgreSQL → `schematool -info` / `-upgradeSchema`
  либо `-initSchema` → `hive --service metastore &` → ожидание порта 9083 → **копирование
  `/opt/tez-ui` в том `tez-ui-static` и запись `config/configs.env`** → `exec hiveserver2` с текущим
  набором `--hiveconf`.
* `hive.metastore.uris` остаётся `thrift://hive-metastore:9083` — алиас указывает на этот же контейнер.
  Embedded-режим запрещён (см. грундинг-бриф).
* Тома `hive-warehouse`, `hive-logs` переезжают сюда; добавляется `tez-ui-static`.
* `hive/scripts/start-metastore.sh`, `start-hiveserver2.sh`, `start-tez-ui.sh` удаляются.

### 5.4 `airflow`

* Entrypoint `airflow/scripts/start-airflow.sh`: `ensure_db.py` → `airflow db init` → `airflow users create`
  → `airflow scheduler &` → `exec airflow webserver`.
* `depends_on: postgres: condition: service_healthy`.
* Healthcheck остаётся `curl --fail http://localhost:8080/health`.
* `airflow/scripts/init-airflow.sh` поглощается новым скриптом.

### 5.5 `webproxy`

* Секция `server { listen 9999; }` в `nginx/nginx.conf` меняет `proxy_pass http://tez-ui:9999` на
  `root` по смонтированному тому `tez-ui-static` (read-only).
* Пока `hive` не наполнил том, nginx отдаёт 404 на :9999 — приемлемо, стенд всё равно прогревается.
* Остальные семь `server`-блоков не меняются: они ходят по DNS-именам `namenode`, `datanode`,
  `hiveserver2`, `spark-history`, которые продолжают резолвиться через алиасы.

### 5.6 Профили `kyuubi` и `jupyter`

* `kyuubi` зависит от `hadoop` и `marquez`, `jupyter` — от `hadoop`. Обе зависимости всегда включены,
  значит модель валидна при любом наборе профилей.
* `tests/test-kyuubi.bat` и тесты, дергающие Jupyter, обязаны падать с внятным сообщением
  «поднимите стенд с `--with-kyuubi`», а не с «container not found».

### 5.7 `start-cluster.bat`

Три правки, все обязательные:

1. Новые флаги `--with-kyuubi`, `--with-jupyter`, `--all` складываются в `COMPOSE_PROFILES`
   (через запятую); compose читает переменную сам.
2. **Этап 1 (`down`) всегда выполняется со всеми профилями:** `COMPOSE_PROFILES=kyuubi,jupyter`.
   Иначе ранее поднятый `kyuubi` или `jupyter` переживёт рестарт стенда и продолжит потреблять
   память — прямое следствие поведения `docker compose down` с профилями (см. грундинг-бриф).
3. `:pull_or_mark` и `:verify_images` для `JUPYTER_IMAGE` и `KYUUBI_IMAGE` становятся условными:
   образы тянутся и проверяются, только если соответствующий профиль включён.

Финальный вывод скрипта перечисляет только реально поднятые интерфейсы и подсказывает флаги для
выключенных.

## 6. Что ломается: осознанная цена

* **Разовый `start-cluster.bat --clean` обязателен при переходе.** Init-скрипт PostgreSQL не
  выполнится на существующем томе `postgres-data`. Стираются HDFS, hive warehouse, метаданные Airflow
  и Marquez. Это должно быть написано в `README.md` и в выводе скрипта.
* **Креды администратора Airflow переезжают на долгоживущий контейнер.** Сейчас `AIRFLOW_ADMIN_*` и
  `AIRFLOW_DB_ADMIN_*` живут только в одноразовом `airflow-init` — это было сознательное решение
  (комментарий в `docker-compose.yml:283-292`). После слияния они попадают в окружение постоянного
  контейнера. Для локального стенда приемлемо, но это регресс, и он фиксируется здесь явно.
* **Гранулярность рестарта падает.** `docker restart hadoop-datanode` больше не существует —
  перезапускается весь `hadoop-node`. То же для метастора отдельно от HiveServer2.
* **Диагностика по логам усложняется**: `docker logs hadoop-node` смешивает вывод шести демонов, и
  это ничем не смягчается — том `hadoop-logs` общий на все шесть демонов, раздельных лог-томов по
  демону в реализации нет. Демоны в `start-hadoop.sh` запускаются напрямую (`hdfs namenode &` и т.д.,
  не через `hadoop-daemon.sh`), поэтому пишут в консоль, а не в отдельные файлы `*-namenode-*.log` —
  единственный способ разобрать вывод конкретного демона это `docker logs hadoop-node`.

## 7. Ожидаемый эффект

Оценки, не замеры (стенд на момент написания спеки не запущен):

* Слияния P1–P7: −1 инстанс PostgreSQL (~40–80 МБ), −1 `python3 -m http.server` (~30–60 МБ),
  −6 оверхедов контейнера (~60–180 МБ). Итого **~150–320 МБ**.
* Профили P9 и P10 при выключенных обоих: дополнительно **~1.1–2.3 ГБ**, плюс два непритянутых
  образа на диске и более быстрый старт стенда.

Тяжёлые JVM всегда включённых сервисов (шесть демонов Hadoop, метастор, HiveServer2, Marquez)
остаются в полном составе — прямое следствие сознательного выбора «полный мердж без профилей для
lineage».

## 8. Проверка

Верификация — существующий набор тестов; отдельных новых тестов дизайн не вводит.

| Проверка | Команда |
| --- | --- |
| Чистый старт с нуля | `start-cluster.bat --clean --build` |
| HDFS | `tests\test-hdfs.bat` |
| YARN | `tests\test-yarn.bat` |
| Hive (метастор + HS2 в одном контейнере) | `tests\test-hive.bat` |
| Spark + Spark History | `tests\test-spark.bat` |
| Airflow (init + scheduler + webserver в одном контейнере) | `tests\test-airflow.bat` |
| OpenLineage → Marquez на общем PostgreSQL | `tests\test-openlineage.bat` |
| Kyuubi под профилем | `start-cluster.bat --with-kyuubi` затем `tests\test-kyuubi.bat` |
| Сквозной прогон | `tests\test-cluster.bat` |
| TEZ UI отдаётся nginx | `curl http://localhost:9999` |
| Три БД в одном PostgreSQL | `docker exec hadoop-postgres psql -U hive -c "\l"` |
| Профильный контейнер не переживает рестарт | `--with-kyuubi`, затем `start-cluster.bat` без флага, затем `docker ps` |

## 9. Ссылки

* Грундинг-бриф целиком содержится в §2 — отдельного внешнего файла нет; таблица §2 и есть
  канонический бриф, который обязан попасть в каждый бриф реализации.
* Предыдущие спеки: `2026-07-21-airflow-container-design.md`, `2026-07-18-openlineage-namespace-resolver-design.md`.
