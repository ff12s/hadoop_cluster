# OpenLineage cluster policy: конфиг из Airflow Variable, тумблер из DAG'а, зонд jar по hdfs-site.xml

**Дата:** 2026-07-29
**Статус:** утверждён (brainstorming), готов к плану реализации
**Артефакт:** `airflow/config/airflow_local_settings.py`, `airflow/config/ol_policy.py` (новый), `airflow/config/hadoop_conf.py` (новый), `airflow/config/tests/` (новый), `airflow/Dockerfile`, `airflow/scripts/start-airflow.sh`, `docker-compose.yml`, `env_example`, `tests/test-policy.bat` (новый), `tests/test-airflow.bat`, `tests/README.md`, `README.md`
**Репозиторий:** `hadoop_cluster` (изменения только здесь)
**Предшественник:** [2026-07-23 per-runtime injection](2026-07-23-openlineage-per-runtime-injection-design.md)

## 1. Контекст и цель

`airflow/config/airflow_local_settings.py` навешивает OpenLineage на каждый `SparkSubmitOperator`
через cluster policy. Текущая реализация читает конфиг **только** из переменных окружения и содержит
зашитые дефолты (`http://marquez:5000`, `hadoop-cluster`), а наличие jar в HDFS проверяет по
захардкоженному порту `9870`, игнорируя `HADOOP_CONF_DIR`.

Цели:

1. Вынести url и namespace в **Airflow Variable**, правимую в UI без пересборки и рестарта.
2. Дать DAG'у возможность **форсированно включить или выключить** сборку лайниджа — на уровне
   отдельной таски и на уровне всего DAG'а.
3. Проверять jar в HDFS по эндпоинтам **из конфигов кластера**, а не по зашитому порту.
4. Не создать при этом нагрузки на PostgreSQL: cluster policy выполняется на **каждом парсе каждого
   DAG-файла**, то есть по умолчанию раз в 30 секунд на файл, бессрочно.

**Не входит в скоуп:** переход на `apache-airflow-providers-openlineage` (требует Airflow ≥ 2.7);
per-task parent-run линковка; namespace-resolver; изменения в соседнем репозитории `SparkAPI`;
kerberos/SPNEGO в зонде HDFS (стенд не керберизован).

## 2. Грундинг-бриф — обязателен во всех брифах реализации

Пины: Airflow **2.6.3** (python3.10), `apache-airflow-providers-apache-spark` **4.1.1**
(проверено по `constraints-2.6.3/constraints-3.10.txt`), Spark **3.5.2**, Scala **2.13.8**,
Hadoop **3.3.6**, OpenLineage **1.46.0**, Marquez **0.47.0**. Executor — `LocalExecutor`,
один контейнер `hadoop-airflow`, PostgreSQL общий с Hive Metastore.

Дельта документации: снапшота 2.6.3 в context7 нет. Семантика cluster policy взята из
`/websites/airflow_apache_apache-airflow_2_11_0`, **каждый** version-sensitive факт перепроверен по
исходникам тега `2.6.3` на `github.com/apache/airflow`. Ссылки на строки ниже — по этим тегам.

| Факт | Источник |
| --- | --- |
| `task_policy` вызывается на **этапе парсинга**: `DagBag._bag_dag` → `for task in dag.tasks: settings.task_policy(task)` | `airflow/models/dagbag.py:480` (тег `2.6.3`); доки: [cluster-policies 2.11.0](https://airflow.apache.org/docs/apache-airflow/2.11.0/administration-and-deployment/cluster-policies.html) — «the `task_policy` executes during task parsing» |
| Любое исключение из политики оборачивается в `AirflowClusterPolicyError` и роняет импорт **всего** DAG-файла | `airflow/models/dagbag.py:481-485` |
| Шедулер форкает **отдельный процесс на каждый DAG-файл на каждый раунд парсинга**; ребёнок сам поднимает и закрывает ORM (`configure_orm` / `dispose_orm`) | `airflow/dag_processing/processor.py:218`, `:150`, `:187` |
| Раунд парсинга — раз в `[scheduler] min_file_process_interval`, дефолт **30** (в `docker-compose.yml` не переопределён) | `airflow/config_templates/config.yml`, тег `2.6.3` |
| Кэш в памяти модуля переживает только один форк, то есть один парс одного файла. Кросс-парсовый TTL-кэш в шедулере не работает **по построению** | следствие из `processor.py:218` |
| Резолв конфига на этапе импорта `airflow_local_settings.py` **невозможен**: `initialize()` вызывает `import_local_settings()` до `configure_orm()` | `airflow/settings.py:522` против `:529` |
| Airflow 2.6.3 **не имеет** кэша секретов: `AIRFLOW__SECRETS__USE_CACHE` / `cache_ttl_seconds` появились в 2.7.0 | [PR #30259](https://github.com/apache/airflow/pull/30259); [разбор фичи](https://medium.com/apache-airflow/the-ins-and-outs-of-airflows-new-secrets-cache-f7b9ec25ca1e) |
| Best-practice Airflow: не читать Variable в top-level/parse-time коде, отложить чтение до выполнения таски через Jinja | [best-practices 2.11.0](https://airflow.apache.org/docs/apache-airflow/2.11.0/best-practices.html), [dynamic-dag-generation](https://airflow.apache.org/docs/apache-airflow/2.11.0/howto/dynamic-dag-generation.html) |
| `SparkSubmitOperator` хранит conf в приватном `self._conf` (публичного `conf` нет), передаёт его в hook на `execute()` | `spark_submit.py:126`, `:166` (тег `providers-apache-spark/4.1.1`) |
| `_conf` **шаблонизируется**: входит в `template_fields`; `render_template` рекурсивно рендерит значения `dict` | `spark_submit.py:75-79`; `airflow/models/abstractoperator.py:167-168` |
| Рендер шаблонов происходит **на воркере**, непосредственно перед `_execute_task` | `airflow/models/taskinstance.py:1531` |
| Jinja-контекст даёт `var.value` / `var.json` (`VariableAccessor`) и `conn` (`ConnectionAccessor`) | `airflow/models/taskinstance.py:2102-2106`; `airflow/utils/context.py:93-132` |
| `Variable.get(..., deserialize_json=True)` делает `json.loads` **без обработки исключений**; `default_var` спасает только от отсутствия переменной, не от битого JSON | `airflow/models/variable.py`, метод `get` |
| `DAG.get_template_env()` кладёт `user_defined_macros` в `env.globals`, окружение строится заново на каждый рендер (`cache_size: 0`) | `airflow/models/dag.py`, метод `get_template_env` |
| `BaseOperator.params` — `ParamsDict` (`MutableMapping`), доступен на парсе без обращений к БД; `__getitem__` резолвит `Param` в сырое значение | `airflow/models/baseoperator.py:885`; `airflow/models/param.py:80-88` |
| `DAG.__init__` **переносит** `default_args["params"]` в `dag.params` и удаляет ключ из `default_args`; `add_task` не подмешивает `dag.params` в `task.params` | `airflow/models/dag.py:437-440` |
| `task_instance_mutation_hook` в 2.6.3 вызывается при создании TI **в шедулере** (`DagRun`), а не на воркере — как площадка для инъекции не годится | `airflow/models/dagrun.py:1219` |
| `EnvironmentVariablesBackend` стоит **первым** в `DEFAULT_SECRETS_SEARCH_PATH`; `get_conn_value` — это `os.environ.get("AIRFLOW_CONN_" + conn_id.upper())` | `airflow/secrets/__init__.py:33`; `airflow/secrets/environment_variables.py:49` |
| `airflow variables get <key>` бросает `SystemExit` при отсутствии ключа → ненулевой код возврата, пригоден для идемпотентного сидинга | `airflow/cli/commands/variable_command.py:41-51` |
| Пустой `spark.extraListeners` **безопасен**: ключ объявлен как `.stringConf.toSequence.createOptional`, `Utils.stringToSeq` фильтрует пустые элементы, `loadExtensions` делает `flatMap` по пустой последовательности | Spark `v3.5.2`: `core/.../internal/config/package.scala:1423-1428`, `core/.../util/Utils.scala:2754-2756`, `:2770-2772`, `core/.../SparkContext.scala:2729` |
| `WebHDFSHook.check_for_path` существует, но берёт конфиг из коннекшена `webhdfs_default` (не из `HADOOP_CONF_DIR`) и делает `connect_ex` + `status("/")` на **каждый** `get_conn()`; провайдера нет в дефолтных extras образа | `airflow/providers/apache/hdfs/hooks/webhdfs.py:55-131` (тег `providers-apache-hdfs/4.1.0`); `Dockerfile:38` (тег `2.6.3`) |
| `pytest` пинуется как **7.4.0** для этой версии Airflow | `constraints-2.6.3/constraints-3.10.txt` |

## 3. Ключевое следствие: где что решается

Стоимость определяется не «сколько запросов», а **где именно** выполняется код. Отсюда разделение:

| Решение | Момент | Обращения к БД |
| --- | --- | --- |
| Это `SparkSubmitOperator`? | парс | 0 |
| Форс вкл/выкл из DAG'а (`params`) | парс | 0 |
| Есть ли jar в HDFS | парс | 0 (1 HTTP GET, ~мс) |
| Глобальный `enabled` | **рендер, воркер** | 1 на запуск таски |
| `transport.url`, `namespace` | **рендер, воркер** | те же |

Шедулер не обращается к метастору за конфигом лайниджа вообще. Это прямое применение
best-practice Airflow «отложить чтение Variable до выполнения таски», ставшее возможным потому, что
`_conf` входит в `template_fields`.

## 4. Архитектура

### 4.1 Раскладка модулей

```
airflow/config/
  airflow_local_settings.py   # только task_policy: гейты, сборка conf, одно присваивание task._conf
  ol_policy.py                # ol_cfg(), lineage_forced(), ol_conf_template(), jar_available()
  hadoop_conf.py              # parse_hadoop_xml + ${var} + кэш по mtime; resolve_webhdfs_urls
  tests/                      # pytest
```

`$AIRFLOW_HOME/config` уже добавляется в `sys.path` (`airflow/settings.py`, `prepare_syspath`),
поэтому соседние модули импортируются как обычные. В `docker-compose.yml` монтаж меняется с одного
файла на каталог:

```yaml
- ./airflow/config:/opt/airflow/config:ro
```

`hadoop_conf.py` — перенос из `SparkAPI/app/core/hadoop_api/hadoop_conf.py` и логики
`HdfsApi._resolve_urls`. Держится отдельным модулем, чтобы расхождение с оригиналом было видно
диффом. Переносится **только** нужное: разбор XML с раскрытием `${var}`, кэш по mtime, резолв
WebHDFS-эндпоинтов. Класс `HadoopHttpClient`, SPNEGO, hedged-failover и `settings` не переносятся.

### 4.2 Поток `task_policy`

```
task_policy(task)
├─ не SparkSubmitOperator            -> return
├─ forced = task.params["openlineage"] ?? task.dag.params["openlineage"]
│  └─ forced is False                -> return                     (conf не трогаем)
├─ jar = os.environ["OPENLINEAGE_JAR"]
│  ├─ пусто                          -> warning, return
│  └─ not jar_available(jar)         -> warning, return
├─ dag.user_defined_macros["ol_cfg"] = ol_cfg      (идемпотентно, не затирая чужие)
├─ ol = ol_conf_template(forced)                   (значения — строки с Jinja)
├─ merged = {**ol, **(task._conf or {})}           DAG-conf побеждает
├─ merged["spark.jars"] = merge_jars(merged.get("spark.jars"), jar)
└─ task._conf = merged                             ЕДИНСТВЕННОЕ присваивание
```

Порядок слияния сохраняется из текущей реализации: явный `conf` в DAG'е побеждает — осознанные
переопределения не затираются. `spark.jars` — исключение из этого правила и обрабатывается **после**
слияния: DAG-значение не отбрасывается, jar дописывается к нему. `merge_jars` разбивает строку по
запятой, отбрасывает пустые элементы, добавляет jar, если его там ещё нет, и склеивает обратно.

Обе мутации выполняются над локальным `merged`; `task._conf` присваивается один раз в самом конце —
этим обеспечивается инвариант 3.

Чтение тумблера: `task.params.get("openlineage")`, затем `task.dag.params.get("openlineage")`, если
`task.dag` не `None`. Значение `None` на любом уровне трактуется как «уровень не высказался» и
передаёт решение ниже по лесенке; на решение влияют только `True` и `False`.

## 5. Контракт конфигурации

### 5.1 Airflow Variable `openlineage_config` (JSON)

```json
{
  "enabled": true,
  "url": "http://marquez:5000",
  "namespace": "hadoop-cluster"
}
```

Правится в UI (Admin → Variables), подхватывается со следующего запуска таски, без рестарта.
Сидится идемпотентно при старте контейнера (§7). Ключи сверх перечисленных игнорируются.

### 5.2 Переменные окружения

| Переменная | Роль | Читается |
| --- | --- | --- |
| `OPENLINEAGE_JAR` | путь к jar в HDFS; обязан совпадать с тем, что заливает `scripts/seed-openlineage-jar.bat` | на парсе, каждый раз |
| `OPENLINEAGE_URL`, `OPENLINEAGE_NAMESPACE` | значения для **первичного сидинга** Variable | один раз при бутстрапе |

`OPENLINEAGE_JAR` остаётся в окружении намеренно: путь к jar — свойство деплоя, и он нужен на парсе,
где Jinja ещё не выполнена. Фолбэка «нет Variable → взять url/namespace из ENV» **нет**: источник
конфигурации ровно один, зашитых дефолтов (`http://marquez:5000`, `hadoop-cluster`) в коде не
остаётся.

### 5.3 Тумблер из DAG'а

Трёхсостоянный ключ `openlineage`, лесенка `task.params` → `dag.params` → Variable:

```python
# весь DAG
with DAG(dag_id="spark_etl_dag", params={"openlineage": False}, ...):

# одна таска — форс поверх выключенного DAG'а
SparkSubmitOperator(task_id="aggregate", params={"openlineage": True}, ...)
```

`default_args={"params": {...}}` тоже работает, но попадает в `dag.params`, а не в `task.params`
(`dag.py:437-440`) — то есть действует как DAG-уровень.

| `task.params` | `dag.params` | Variable `enabled` | Итог | Где решается |
| --- | --- | --- | --- | --- |
| `False` | — | — | выкл | парс |
| `True` | — | — | вкл | парс |
| нет | `False` | — | выкл | парс |
| нет | `True` | — | вкл | парс |
| нет | нет | `true` | вкл | рендер |
| нет | нет | `false` / нет Variable / битый JSON | выкл | рендер |

**Форс не обходит зонд jar.** `params={"openlineage": True}` при недоступном jar лайнидж не включает —
иначе вернулся бы `ClassNotFoundException`, ради которого зонд и писался (коммит `9b6775b`). В лог
уходит warning с `dag_id` и `task_id`, чтобы расхождение «включил, а лайниджа нет» находилось грепом.

### 5.4 Инжектируемые ключи

```python
LISTENER = "io.openlineage.spark.agent.OpenLineageSparkListener"

{
  "spark.extraListeners": (
      LISTENER if forced is True
      else "{{ " + repr(LISTENER) + " if ol_cfg().get('enabled') else '' }}"
  ),
  "spark.openlineage.transport.type": "http",
  "spark.openlineage.transport.url": "{{ ol_cfg().get('url', '') }}",
  "spark.openlineage.namespace": "{{ ol_cfg().get('namespace', '') }}",
  "spark.openlineage.columnLineage.datasetLineageEnabled": "true",
}
```

Пустой `spark.extraListeners` безопасен — обосновано в бриф-таблице по исходникам Spark 3.5.2.

`ol_cfg` — макрос, который политика кладёт в `dag.user_defined_macros` на парсе:

```python
def ol_cfg() -> dict[str, object]:
    """Конфиг OL из Airflow Variable. Никогда не бросает: при любой ошибке — пустой dict."""
    try:
        raw = Variable.get("openlineage_config", default_var=None)
        return json.loads(raw) if raw else {}
    except Exception:
        return {}
```

Почему макрос, а не `{{ var.json.openlineage_config.url }}`: `Variable.get(deserialize_json=True)`
делает `json.loads` без обработки, поэтому **битый JSON в существующей переменной уронил бы рендер и
таску**. `default_var` от этого не защищает — он покрывает только отсутствие переменной. Макрос
переносит `json.loads` в Python под `try/except` и делает шаблон невозможным к падению.

Секреты в conf не попадают: политика инжектит только `transport.type` и `transport.url`. Если в
Variable появится `auth` — пишется warning «не поддерживается», подстановки не происходит. Причина:
любое значение из `_conf` уезжает в командную строку `spark-submit` и видно в `ps` и в YARN.

## 6. Зонд jar в HDFS

Резолв эндпоинтов повторяет `SparkAPI/app/core/hadoop_api/hdfs_api.py::HdfsApi._resolve_urls`:

1. `dfs.http.policy == "HTTPS_ONLY"` → схема `https` и ключ `dfs.namenode.https-address`,
   иначе `http` и `dfs.namenode.http-address`.
2. HA: для каждого `dfs.nameservices` → `dfs.ha.namenodes.<ns>` → `<addr_key>.<ns>.<nn>`.
3. Не HA: одиночный `<addr_key>`.
4. Фолбэк: хост из `fs.defaultFS` (`core-site.xml`) + `9870` / `9871`.

Запрос — `GETFILESTATUS` по эндпоинтам подряд до первого ответившего, таймаут 4 с (как сейчас).
`404` → `False` без warning (штатное «jar не залит»); сетевая ошибка или иной код → `False` с warning.
Разбор `*-site.xml` кэшируется по mtime, что и даёт подхват изменённого конфига без рестарта.

`${env.VAR}` и `${system.prop}` не раскрываются — как и в оригинале; конфиги стенда такой формы
не используют.

**Принятый компромисс:** `spark.jars` дописывается всегда, когда зонд успешен, даже если Variable
выключает лайнидж — на парсе значение `enabled` неизвестно. Цена — локализация одного jar YARN'ом
на джобу. Альтернатива требует шаблонизировать слияние списков jar'ов в Jinja и читается заметно
хуже.

## 7. Сидинг Variable

В `airflow/scripts/start-airflow.sh`, после `db upgrade`, до старта планировщика:

```sh
airflow variables get openlineage_config >/dev/null 2>&1 \
  || airflow variables set --json openlineage_config "$OPENLINEAGE_CONFIG_JSON"
```

`OPENLINEAGE_CONFIG_JSON` собирается из `OPENLINEAGE_URL`, `OPENLINEAGE_NAMESPACE` и константы
`"enabled": true`. Идемпотентность обязательна: правка через UI обязана переживать рестарт
контейнера. `variables get` при отсутствии ключа завершается ненулевым кодом (`variable_command.py:41-51`).

## 8. Инварианты

1. **Политика никогда не бросает и никогда не влияет на успешность парса или запуска DAG'а.**
   Любая ошибка — нет конфига, недоступен HDFS, битый XML, не смонтирован `HADOOP_CONF_DIR`,
   неожиданный тип в `params` — гасится, пишется в лог, лайнидж просто не включается. Единственный
   наблюдаемый эффект отказа — отсутствие лайниджа. Тело `task_policy` целиком под
   `except Exception` с `_log.warning(..., exc_info=True)`. Основание: `dagbag.py:481-485` роняет
   импорт **всего файла**, то есть баг в политике выключил бы все DAG'и разом.
2. **Ни один шаблон, оставленный политикой, не может уронить рендер.** Все обращения к Variable идут
   через макрос `ol_cfg`, который не бросает. Прямые `{{ var.json.* }}` в инжектируемом conf
   запрещены.
3. **Никакой частичной мутации.** `task._conf` присваивается **одним** финальным присваиванием после
   того, как словарь собран целиком; исключение на полпути не может оставить таску с половиной
   OL-ключей.
4. **conf из DAG'а побеждает.** Порядок слияния `{**ol, **(task._conf or {})}` сохраняется;
   `spark.jars` дописывается, а не перезаписывается.
5. **Секреты не уезжают в argv.** Значения Variable в лог не пишутся; `transport.auth` не
   подставляется.
6. **Ноль обращений к метастору на парсе.** Ни `Variable.get`, ни `BaseHook.get_connection` в коде,
   выполняемом на этапе парсинга.

## 9. Тесты

Юнит-тестов в репозитории нет — только `.bat`-смоуки против живого стенда. Заводится минимум,
достаточный для TDD по этой задаче.

Инфраструктура: `pytest==7.4.0` в `airflow/Dockerfile` (пин из constraints-2.6.3), тесты в
`airflow/config/tests/`. `ol_policy.py` и `hadoop_conf.py` не импортируют Airflow на уровне модуля —
импорт оператора и `Variable` внутри функций, как в текущем коде.

RED-набор:

- таблица истинности тумблера — все 6 строк §5.3;
- форс `True` при недоступном jar не включает лайнидж, пишет warning;
- `resolve_webhdfs_urls`: HA-список, одиночный `http-address`, фолбэк на `fs.defaultFS`, `HTTPS_ONLY`;
- зонд: `200` → `True`; `404` → `False` без warning; сетевая ошибка → `False` с warning; перебор
  эндпоинтов до первого ответившего;
- `ol_cfg`: отсутствие Variable → `{}`; битый JSON → `{}`; валидный JSON → dict; ни один случай не
  бросает;
- **инвариант 1**: политика не бросает и не мутирует `_conf` при `HADOOP_CONF_DIR=/nonexistent`,
  недостижимом NameNode, мусоре в `OPENLINEAGE_JAR`, `params={"openlineage": "yes"}`;
- инвариант 3: `_conf` не изменён, если исключение произошло после начала сборки словаря;
- `_conf`, заданный в DAG'е, не затирается; `spark.jars` дописывается;
- не-`SparkSubmitOperator` не трогается;
- `dag.user_defined_macros` не затирает уже существующие макросы DAG'а.

Смоуки:

- `tests/test-policy.bat` → `docker exec hadoop-airflow python -m pytest /opt/airflow/config/tests -q`;
- `tests/test-airflow.bat` расширяется: `params={"openlineage": False}` убирает листенер из
  фактически отправленной команды; правка Variable подхватывается следующим запуском без рестарта.

## 10. Изменения по файлам

| Файл | Изменение |
| --- | --- |
| `airflow/config/airflow_local_settings.py` | сводится к `task_policy`: гейты, сборка, одно присваивание |
| `airflow/config/ol_policy.py` | новый: `ol_cfg`, `lineage_forced`, `ol_conf_template`, `jar_available` |
| `airflow/config/hadoop_conf.py` | новый: `parse_hadoop_xml`, `resolve_webhdfs_urls` |
| `airflow/config/tests/` | новый: pytest-набор §9 |
| `airflow/Dockerfile` | `pytest==7.4.0` под тем же constraints-файлом |
| `airflow/scripts/start-airflow.sh` | идемпотентный сидинг Variable |
| `docker-compose.yml` | монтаж каталога `./airflow/config` вместо одного файла |
| `env_example` | комментарии к `OPENLINEAGE_*`: что читается в рантайме, что только при сидинге |
| `tests/test-policy.bat` | новый |
| `tests/test-airflow.bat`, `tests/README.md`, `README.md` | описание тумблера и Variable |

## 11. Риски и что проверить на этапе плана

- **`defusedxml` в образе.** Пинуется в constraints-2.6.3 (`0.7.1`) и приходит транзитивно с
  Flask-AppBuilder, но это надо подтвердить командой в контейнере, а не предположением. Если пакета
  нет — использовать stdlib `xml.etree.ElementTree`: файлы свои, смонтированы `:ro`, внешнего ввода
  нет. Решение зафиксировать в плане, а не в реализации.
- **`SandboxedEnvironment`.** Airflow рендерит DAG-шаблоны в песочнице; вызов глобальной функции и
  `dict.get` в ней разрешены, но это стоит подтвердить фактическим запуском таски, а не только
  юнит-тестом.
- **Мутация `user_defined_macros` из политики.** Механика подтверждена по `DAG.get_template_env`
  (`env.globals.update`, `cache_size: 0`), но это нестандартный приём — требует явного комментария
  в коде, иначе появление `ol_cfg` в DAG'е «из ниоткуда» нечитаемо.
- **Совместимость с существующими DAG'ами.** `spark_pi_dag` и `spark_etl_dag` не задают `params`;
  после изменения обе таски должны продолжать писать лайнидж при `enabled: true` в Variable.
