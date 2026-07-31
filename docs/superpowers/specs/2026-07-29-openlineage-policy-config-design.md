# OpenLineage cluster policy: конфиг из Airflow Variable, тумблер из DAG'а, зонд jar по hdfs-site.xml

**Дата:** 2026-07-29 (ревизия 3 — две целевые среды, см. §1.1 и §12)
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
5. Не создать при этом нового способа уронить DAG или джобу: ни исключением, ни временем,
   ни включением лайниджа с неполным конфигом.

**Про цель 3 — честный мотив.** На этом стенде она не меняет ни одного байта поведения:
`base/config/hdfs-site.xml` не содержит ни `dfs.nameservices`, ни `dfs.namenode.http-address`,
ни `dfs.http.policy`, поэтому резолв гарантированно уходит в фолбэк «хост из `fs.defaultFS` + 9870»
и даёт ровно текущий захардкоженный `http://namenode:9870`. Мотив цели 3 — **не** «подхват изменённого
конфига без рестарта» (см. ниже, почему это на стенде невозможно), а снятие допущения «NameNode
всегда один и всегда на 9870», которое ломается на первом же HA-контуре. Цена принята осознанно.

**Не входит в скоуп:** переход на `apache-airflow-providers-openlineage` как на источник Spark-лайниджа;
per-task parent-run линковка; namespace-resolver; изменения в соседнем репозитории `SparkAPI`;
kerberos/SPNEGO в зонде HDFS (стенд не керберизован).

## 1.1 Две целевые среды — жёсткое требование

Один и тот же код политики обязан работать в **обоих** окружениях, описанных файлами
`airflow/requirements.txt` и `airflow/requirements_cloud.txt`. Это не «желательно»: различия между
ними ломают ключевое допущение ревизии 2.

| | `requirements.txt` (стенд) | `requirements_cloud.txt` (облако) |
| --- | --- | --- |
| Airflow | **2.6.3** | **2.10.2** |
| `apache-airflow-providers-apache-spark` | **4.1.1** | **4.10.0** |
| Атрибуты `SparkSubmitOperator` | приватные: `_conf`, `_jars` | **публичные: `conf`, `jars`** |
| `template_fields` | `"_conf"`, `"_jars"` | `"conf"`, `"jars"` |
| `apache-airflow-providers-apache-hdfs` | 4.1.0 | 4.5.0 |
| `apache-airflow-providers-openlineage` | нет | **1.11.0** |
| `defusedxml` | **нет** | 0.7.1 |
| `pytest` | **нет** | 8.3.3 |
| `[secrets] use_cache` | опции не существует | есть, дефолт `False` |
| `pyspark` | 3.3.2 | 3.5.2 |

Следствия, определяющие дизайн:

1. **Имя атрибута conf нельзя зашивать.** Ревизия 2 писала `task._conf` и `task._jars`. На облачной
   ветке такое присваивание не упало бы — оно создало бы **новые атрибуты, которых никто не читает**:
   `_get_hook` там берёт `self.conf` и `self.jars`. Отказ молчаливый и полный: ни ошибки, ни лайниджа.
   Правило — в §4.2.
2. **Разрешено только то, что есть в обеих средах.** Пересечение — стандартная библиотека плюс ядро
   Airflow. Значит разбор XML — `xml.etree.ElementTree` из stdlib (`defusedxml` в стенде нет), зонд —
   `urllib` (как сейчас), запущенный в отдельном демон-потоке `threading.Thread` ради жёсткого
   дедлайна (§6.1: таймаут сокета не ограничивает резолв имени), никаких новых зависимостей.
3. **`pytest` не добавляется в `requirements.txt`.** Тестовый набор ставится отдельно и обязан
   проходить и на 7.x, и на 8.x: никаких возможностей, появившихся в 8, и никаких удалённых в 8
   (nose-style `setup`/`teardown`, неявные async-тесты без плагина).
4. **`apache-airflow-providers-apache-hdfs` присутствует в обеих средах.** Отказ от `WebHDFSHook` —
   решение по поведению, а не по доступности (обоснование в §2 и §6), и его нужно держать явным,
   потому что лестница переиспользования на этот пакет указывает.
5. **Сидинг Variable (§7) — стендовая деталь.** Он живёт в `airflow/scripts/start-airflow.sh`, а тот
   в облаке не исполняется; там же `airflow db init` уже помечен устаревшим в пользу `db migrate`.
   Облако обязано создать Variable своими средствами либо задать `AIRFLOW_VAR_OPENLINEAGE_CONFIG`.
   Политика не должна зависеть от того, кто именно создал Variable.

## 2. Грундинг-бриф — обязателен во всех брифах реализации

Пины стенда: Airflow **2.6.3** (python3.10), `apache-airflow-providers-apache-spark` **4.1.1**
(проверено по `constraints-2.6.3/constraints-3.10.txt` и по `airflow/requirements.txt`), Spark
**3.5.2**, Scala **2.13.8**, Hadoop **3.3.6**, OpenLineage **1.46.0**, Marquez **0.47.0**.
Executor — `LocalExecutor`, один контейнер `hadoop-airflow`, PostgreSQL общий с Hive Metastore.

Пины второй целевой среды (`airflow/requirements_cloud.txt`): Airflow **2.10.2**,
`apache-airflow-providers-apache-spark` **4.10.0**, `apache-airflow-providers-openlineage` **1.11.0**,
`pyspark` **3.5.2**, `defusedxml` **0.7.1**, `pytest` **8.3.3**. Различия и их последствия — §1.1.
Каждый факт ниже помечен тем, к какой среде он относится; помеченный «обе» проверен по обоим тегам.

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
| `task_instance_mutation_hook` получает `TaskInstance`, а не таску на парсе; по исходнику он вызывается при создании TI в `DagRun` (шедулер), докстринг `airflow/policies.py` — «Allow altering task instances before being queued by the Airflow scheduler». **Прозаический раздел доков 2.11.0 при этом утверждает исполнение на воркере** — расхождение доков с исходником не разрешено и вынесено в §11. Дизайн на этот хук не опирается: даже при воркерном вызове он не покрывает парс-часть инъекции (jar в атрибут jars, зонд), то есть был бы **второй** площадкой, а не единственной | `airflow/models/dagrun.py:1219`, `airflow/policies.py` (тег `2.6.3`); [cluster-policies 2.11.0](https://airflow.apache.org/docs/apache-airflow/2.11.0/administration-and-deployment/cluster-policies.html), раздел «Available Policy Functions» |
| `EnvironmentVariablesBackend` стоит **первым** в `DEFAULT_SECRETS_SEARCH_PATH`; `get_conn_value` — это `os.environ.get("AIRFLOW_CONN_" + conn_id.upper())` | `airflow/secrets/__init__.py:33`; `airflow/secrets/environment_variables.py:49` |
| `airflow variables get <key>` бросает `SystemExit` при отсутствии ключа → ненулевой код возврата, пригоден для идемпотентного сидинга | `airflow/cli/commands/variable_command.py:41-51` |
| Пустой `spark.extraListeners` **безопасен**: ключ объявлен как `.stringConf.toSequence.createOptional`, `Utils.stringToSeq` фильтрует пустые элементы, `loadExtensions` делает `flatMap` по пустой последовательности | Spark `v3.5.2`: `core/.../internal/config/package.scala:1423-1428`, `core/.../util/Utils.scala:2754-2756`, `:2770-2772`, `core/.../SparkContext.scala:2729` |
| `WebHDFSHook.check_for_path` существует, но берёт конфиг из коннекшена `webhdfs_default` (не из `HADOOP_CONF_DIR`) и делает `connect_ex` + `status("/")` на **каждый** `get_conn()` | `airflow/providers/apache/hdfs/hooks/webhdfs.py:55-131` (тег `providers-apache-hdfs/4.1.0`) |
| `pytest` пинуется как **7.4.0** для этой версии Airflow | `constraints-2.6.3/constraints-3.10.txt` |

Ревизия 2 — факты, перепроверенные по исходникам и опровергнувшие следствия ревизии 1:

| Факт | Источник |
| --- | --- |
| `airflow variables set --json` означает **сериализовать**, а не «значение уже JSON»: `variables_set` → `Variable.set(key, value, serialize_json=args.json)`, а `Variable.set` при `serialize_json=True` делает `json.dumps(value, indent=2)`. Подача готовой JSON-строки с `--json` кодирует её **дважды** | `airflow/cli/commands/variable_command.py` (метод `variables_set`), `airflow/models/variable.py` (метод `set`), тег `2.6.3` |
| `Variable.get` без `deserialize_json` возвращает сохранённую строку как есть; UI (Admin → Variables) сохраняет введённое поле `Val` тоже как есть. Значит сидинг и UI обязаны писать **одинаково** — сырой строкой | там же, метод `get` |
| `timeout(dagbag_import_timeout)` оборачивает **только** `parse(mod_name, filepath)` внутри `_load_modules_from_file`. `bag_dag` / `_bag_dag`, а с ними и `task_policy`, исполняются **после** и **вне** этого таймаута | `airflow/models/dagbag.py`, `_load_modules_from_file` против `_process_modules`, тег `2.6.3` |
| Следствие: зависший сетевой вызов в политике не даёт исключения — он расходует бюджет `[core] dag_file_processor_timeout` (дефолт **50**), по исчерпании которого убивают процесс парсинга файла и **DAG пропадает из UI**. `try/except` этот отказ не покрывает **по построению** | там же + `config_templates/config.yml`, тег `2.6.3` |
| `AirflowTaskTimeout` — наследник `AirflowException` → `Exception`, то есть перехватывается голым `except Exception` | `airflow/exceptions.py`, тег `2.6.3` |
| `SparkSubmitOperator.jars` (`_jars`) уходит в hook как `--jars`. В `SparkSubmitArguments.loadEnvironmentArguments` стоит `jars = Option(jars).orElse(sparkProperties.get(config.JARS.key)).orNull` — то есть **явный `--jars` побеждает** `spark.jars`, поданный через `--conf`, как источник значения | Spark `v3.5.2`: `core/.../deploy/SparkSubmitArguments.scala`, `loadEnvironmentArguments`; провайдер 4.1.1: `spark_submit.py::_get_hook` |
| Стенд: `base/config/hdfs-site.xml` задаёт только `dfs.replication`, `dfs.namenode.rpc-address`, `*.dir`, `dfs.permissions.enabled` — ни HA, ни `http-address`, ни `dfs.http.policy`. `core-site.xml`: `fs.defaultFS=hdfs://namenode:9000` | код репозитория |
| Стенд: `HADOOP_CONF_DIR=/opt/hadoop/etc/hadoop` в airflow-контейнере **не смонтирован**, а запечён в образ (`COPY --from=hadoopdist /opt/hadoop`). Файлы конфигов меняются только пересборкой образа → кэш разбора XML по mtime не может ничего «подхватить без рестарта» | `airflow/Dockerfile:32`; `docker-compose.yml`, `x-airflow-volumes` |
| ~~DAG-файлы парсит и webserver~~ — **утверждение ревизии 2 неверно и снято ревизией 3.** Webserver наполняет `DagBag` из таблицы `serialized_dag` (`read_dags_from_db=True`, `collect_dags_from_db`) и DAG-файлы не разбирает, поэтому ни `_bag_dag`, ни `task_policy`, ни зонд HDFS в веб-процессе не исполняются. `task_policy` работает ровно в двух местах, и оба — **короткоживущие** процессы: форк DAG-file-processor'а шедулера (один на файл на раунд) и процесс запуска таски на воркере, который разбирает DAG-файл заново | доки [DAG Serialization 2.11.0](https://airflow.apache.org/docs/apache-airflow/2.11.0/administration-and-deployment/dag-serialization.html): «DAG Serialization allows the Airflow Webserver to operate statelessly by decoupling it from the need to parse DAG files directly… The Webserver then retrieves these serialized DAGs to populate the UI»; `airflow/models/dagbag.py`, `collect_dags_from_db` |
| `ParamsDict` наследует `MutableMapping`, чей `get` ловит только `KeyError`; `ParamsDict.__getitem__` резолвит `Param` и **валидирует** его, бросая `ParamValidationError` — она сквозь `.get` проходит | `airflow/models/param.py`, классы `Param`, `ParamsDict`, тег `2.6.3` |
| `EnvironmentVariablesBackend` первым в цепочке распространяется и на Variable: `AIRFLOW_VAR_OPENLINEAGE_CONFIG` перекроет значение из метастора, и правка в UI перестанет действовать | `airflow/secrets/__init__.py:33`; `airflow/secrets/environment_variables.py` |

Ревизия 3 — факты второй целевой среды и то, что они меняют:

| Факт | Среда | Источник |
| --- | --- | --- |
| **Атрибуты `SparkSubmitOperator` переименованы в публичные.** В 4.10.0 конструктор пишет `self.conf`, `self.jars`, а `_get_hook` читает `self.conf` / `self.jars`; `template_fields` содержит `"conf"`, `"jars"` — **без** подчёркивания. В 4.1.1 всё это приватное: `self._conf`, `self._jars`, `template_fields` с `"_conf"`, `"_jars"` | различаются | `spark_submit.py`, теги `providers-apache-spark/4.1.1` и `providers-apache-spark/4.10.0` |
| Следствие: присваивание по фиксированному имени `task._conf` на 4.10.0 создаёт **неиспользуемый** атрибут — ни исключения, ни лайниджа. Отказ невидим и в логе, и в UI | облако | там же |
| `task_policy` по-прежнему вызывается из `_bag_dag` и по-прежнему **вне** `timeout(dagbag_import_timeout)`: тот оборачивает только `parse(mod_name, filepath)` в `_load_modules_from_file` | обе | `airflow/models/dagbag.py`, теги `2.6.3` и `2.10.2` |
| В 2.10.2 наружу пробрасываются `AirflowClusterPolicyViolation` **и** `AirflowClusterPolicySkipDag`; всё остальное заворачивается в `AirflowClusterPolicyError` | 2.10.2 | там же |
| `dag_file_processor_timeout` лежит в секции **`[core]`**, а не `[scheduler]`; дефолт **50**. `[core] dagbag_import_timeout` — **30.0**, `[scheduler] min_file_process_interval` — **30** | обе, значения совпадают | `airflow/config_templates/config.yml`, теги `2.6.3` и `2.10.2` |
| `variables set --json` и в 2.10.2 означает сериализацию: `variables_set` вызывает `Variable.set(args.key, args.value, args.description, serialize_json=args.json)`, а `Variable.set` делает `json.dumps(value, indent=2)`. Ловушка двойного кодирования одинакова; сигнатура отличается только позиционным `description` | обе | `airflow/cli/commands/variable_command.py`, `airflow/models/variable.py`, теги `2.6.3` и `2.10.2` |
| `variables get` при отсутствии ключа поднимает `SystemExit` в обеих версиях — идемпотентный сидинг работает одинаково | обе | там же |
| В 2.10.2 `Variable.get` проходит через `SecretCache`, а `set`/`update`/`delete` его инвалидируют. Но `[secrets] use_cache` по умолчанию **False**, а описание опции — «Enables local caching of Variables, **when parsing DAGs only**»: кэш фреймворка не покрывает чтение на рендере и мемоизацию внутри макроса не дублирует | 2.10.2 | `airflow/models/variable.py`, `config_templates/config.yml`, тег `2.10.2` |
| `$AIRFLOW_HOME/config` кладётся в `sys.path` и в 2.10.2 (функция переименована в `prepare_syspath_for_config_and_plugins`), а `import_local_settings()` по-прежнему вызывается **до** `configure_orm()` | обе | `airflow/settings.py`, теги `2.6.3` и `2.10.2` |
| `apache-airflow-providers-openlineage` 1.11.0 объявляет `disabled`, `disabled_for_operators`, `selective_enable`, `namespace`, `extractors`, `custom_run_facets`, `config_path`, `transport`, `disable_source_code`, `dag_state_change_process_pool_size`, `execution_timeout`, `include_full_task_info`, `debug_mode`. Опции инжекции parent-job в Spark-конфиг среди них **нет** — второго писателя в `conf` таски не появляется | облако | `airflow/providers/openlineage/provider.yaml`, тег `providers-openlineage/1.11.0` |
| `defusedxml` есть только в облачной среде; `pytest` — тоже (8.3.3), в стендовой не пинуется вовсе | различаются | `airflow/requirements.txt`, `airflow/requirements_cloud.txt` |
| `apache-airflow-providers-apache-hdfs` присутствует в **обеих** средах (4.1.0 и 4.5.0) — отказ от `WebHDFSHook` обоснован его поведением, а не отсутствием пакета | обе | там же |
| **Таймаут сокета не ограничивает резолв имени.** `timeout` у `urlopen`/`create_connection` — это таймаут операций **на объекте сокета** (`settimeout`: «operations time out after timeout seconds»); разрешение имени делает отдельный модульный вызов `socket.getaddrinfo`, у которого параметра таймаута нет вовсе (см. сигнатуру и dual-stack-пример в `Doc/library/socket.rst`). Зависший DNS не прерывается ни `timeout=2`, ни `socket.setdefaulttimeout` | обе (stdlib) | CPython `Doc/library/socket.rst`, `Doc/howto/urllib2.rst`, `Modules/socketmodule.c` (`sock_settimeout`), тег `v3.11.14` |
| **Демон-поток — единственный stdlib-примитив с гарантированным дедлайном.** `threading._shutdown` ждёт только **не**-демонические потоки, демонические на выходе интерпретатора не джойнятся. `ThreadPoolExecutor` для этой роли **непригоден**: его потоки не демонические и «are joined before the interpreter exits» — зависший воркер пула подвесил бы выход процесса парсинга | обе (stdlib) | CPython `Lib/threading.py` (`_shutdown`), `Doc/library/concurrent.futures.rst` (ThreadPoolExecutor), тег `v3.11.14` |
| Полное имя класса листенера — `io.openlineage.spark.agent.OpenLineageSparkListener`; сопутствующие ключи `spark.openlineage.transport.type` / `.url` / `spark.openlineage.namespace` | обе | доки OpenLineage, `website/docs/integrations/spark/configuration/usage.md`; то же значение уже стоит в текущем `airflow/config/airflow_local_settings.py:95` |
| Standby-NameNode отвечает **`403` + `RemoteException.exception == "StandbyException"`**; в оригинале резолвера это не терминальный отказ, а сигнал «взять следующий эндпоинт» | обе | `SparkAPI/app/core/hadoop_api/hdfs_api.py`, `_is_standby` / `_handle_response` (строки 64-90) |

Чего ревизия 3 **не** перепроверяла по тегу 2.10.2 (вынесено в §11 как непроверенное для облака, а не выдано за проверенное):
лесенка `params` (`DAG.__init__` переносит `default_args["params"]` в `dag.params`; `add_task` не подмешивает
`dag.params` в `task.params`; `ParamsDict.__getitem__` бросает `ParamValidationError`), доставка макроса
через `DAG.get_template_env` → `env.globals`, и попадание `MappedOperator` в `dag.tasks` на входе `task_policy`.

## 3. Ключевое следствие: где что решается

Стоимость определяется не «сколько запросов», а **где именно** выполняется код. Отсюда разделение:

| Решение | Момент | Где исполняется | Обращения к БД | Сеть |
| --- | --- | --- | --- | --- |
| Это подходящая таска (тип и раскладка атрибутов)? | парс | форк DAG-file-processor'а, процесс запуска таски на воркере | 0 | нет |
| Форс вкл/выкл из DAG'а (`params`) | парс | там же | 0 | нет |
| Есть ли jar в HDFS | парс | там же | 0 | ≤1 **вызов зонда** на парс файла (мемо); внутри вызова — до одного GET на эндпоинт, весь перебор под дедлайном 5 с |
| Применение `enabled` и подстановка `url`, `namespace` | **рендер** | только воркер | **1 на запуск таски** (мемо на процесс) | нет |

Три уточнения к ревизии 1, каждое — исправление занижения:

1. Парс идёт не только в шедулере: воркер перед запуском таски разбирает DAG-файл заново, и
   `task_policy` отрабатывает там тоже. Webserver в этот список **не** входит — он читает
   сериализованные DAG'и из БД и файлы не парсит (ревизия 2 утверждала обратное, §2). Оба процесса,
   где политика действительно исполняется, короткоживущие, но полагаться на это в ограничителях
   нельзя: см. §6.1, где дедлайн зонда сделан **повызовным**, а мемо — c TTL, поэтому ни граница
   времени, ни отрицательный результат не залипают навсегда ни в каком процессе.
2. Зонд без мемоизации — это поход в сеть **на каждую таску**, а не на файл. У `spark_etl_dag` их две.
   Мемо на процесс (`dict` в модуле) сводит их к одному вызову зонда на парс. Число **HTTP-запросов**
   внутри вызова мемо не ограничивает: перебор идёт по эндпоинтам до первого осмысленного ответа
   (§6), то есть верхняя граница — число эндпоинтов, а не единица. На стенде эндпоинт всегда один
   (фолбэк, §1), на HA-контуре — сколько NameNode. Ограничивает эту ветку не счётчик запросов,
   а дедлайн на весь перебор (§6.1).
3. Конфиг лайниджа читается в **трёх** значениях conf таски (листенер, url, namespace). Без
   мемоизации это три `Variable.get`, каждый со своей сессией и проходом по цепочке секрет-бэкендов,
   — а не одно обращение, как утверждала ревизия 1. Мемо на процесс воркера сводит их к одному.

Шедулер не обращается к метастору за конфигом лайниджа вообще. Это прямое применение
best-practice Airflow «отложить чтение Variable до выполнения таски», ставшее возможным потому, что
атрибут conf оператора (его имя резолвит `operator_attrs`, §4.2) входит в `template_fields`.

## 4. Архитектура

### 4.1 Раскладка модулей

```
airflow/config/
  airflow_local_settings.py   # только task_policy: одна строка — делегат в ol_policy.apply_policy
  ol_policy.py                # LISTENER, MACRO; apply_policy(), inject_openlineage(),
                              # ol_macro(), _cfg(), _clean(), _warn_once(), lineage_forced(),
                              # ol_conf_template(), jar_available(), merge_jars(), reset_state(),
                              # operator_attrs()  <- совместимость двух провайдеров
  hadoop_conf.py              # parse_hadoop_xml + ${var}; resolve_webhdfs_urls
  tests/                      # pytest + conftest.py (sys.path + autouse-сброс состояния)
```

**Границы двух функций политики зафиксированы — это контракт, а не деталь реализации.** Требование §9
«гейт типа отделён от сборки» реализуется так:

| Функция | Модуль | Что делает |
| --- | --- | --- |
| `task_policy(task)` | `airflow_local_settings.py` | ровно одна строка: `ol_policy.apply_policy(task)`. Файл и имя диктует Airflow, логики в нём нет |
| `apply_policy(task)` | `ol_policy.py` | тело целиком под `try/except` (инвариант 1) + двухступенчатый гейт типа из §4.2; при успешном гейте зовёт `inject_openlineage(task)` |
| `inject_openlineage(task)` | `ol_policy.py` | принимает **уже проверенную** таску и делает всё остальное: `operator_attrs`, тумблер, гейты conf/dag/макроса, зонд, сборку и три финальные мутации. Может бросать — исключение ловит `apply_policy` |

Тесты раскладок атрибутов и таблицы истинности бьют по `inject_openlineage`, тесты инварианта 1 и
гейта типа — по `apply_policy`. Обе живут в `ol_policy.py`, поэтому **тестам не нужно импортировать
`airflow_local_settings.py`** (импорт которого Airflow выполняет своим бутстрапом и который тянет
`SparkSubmitOperator` установленной версии, то есть ровно одну из двух раскладок).

`$AIRFLOW_HOME/config` уже добавляется в `sys.path` (`airflow/settings.py`, `prepare_syspath`),
поэтому соседние модули импортируются как обычные. В `docker-compose.yml` монтаж меняется с одного
файла на каталог:

```yaml
- ./airflow/config:/opt/airflow/config:ro
```

`hadoop_conf.py` — перенос из `SparkAPI/app/core/hadoop_api/hadoop_conf.py` и логики
`HdfsApi._resolve_urls`. Держится отдельным модулем, чтобы расхождение с оригиналом было видно
диффом. Переносится **только** нужное: разбор XML с раскрытием `${var}` и резолв WebHDFS-эндпоинтов.
Класс `HadoopHttpClient`, SPNEGO, hedged-failover и `settings` не переносятся.

**Разбор XML — stdlib `xml.etree.ElementTree`.** `defusedxml` есть только в облачной среде (§1.1),
а зависимость, отсутствующая в одной из целевых сред, — не зависимость. Риск приемлем и ограничен:
разбираются собственные файлы кластера, смонтированные только на чтение, внешнего ввода в них нет.
Это решение, а не открытый вопрос: ревизия 2 оставляла его на этап плана.

**Кэш разбора XML по mtime не переносится.** В оригинале он оправдан долгоживущим процессом
и смонтированными конфигами; здесь конфиги запечены в образ (`airflow/Dockerfile:32`), а процесс
живёт один парс — инвалидировать нечего и переживать нечему. Разбор двух небольших XML стоит единицы
миллисекунд и повторяется раз на процесс. Меньше кода, меньше расхождений с оригиналом, ноль потери.

### 4.2 Поток политики

```
apply_policy(task)                                 # тело целиком под try/except (§8, инвариант 1)
├─ не экземпляр SparkSubmitOperator
│  ├─ но operator_class/task_type — SparkSubmitOperator -> warning «маппинг», return  (инвариант 9)
│  └─ иначе                          -> return                     (тихо, это не наша таска)
└─ inject_openlineage(task)                        # дальше — уже проверенная таска

inject_openlineage(task)
├─ attrs = operator_attrs(task)                    # какие имена у conf/jars в ЭТОМ провайдере
│  └─ None                           -> warning, return            (§8, инвариант 8)
├─ forced = lineage_forced(task)                   # task.params -> dag.params, только True/False
│  └─ forced is False                -> return                     (тихо: это решение DAG'а)
├─ cur_conf = dict(getattr(task, attrs.conf) or {})
│  └─ в conf есть ЧУЖОЙ "spark.extraListeners" -> warning, return  (§8, инвариант 9)
├─ dag = task.dag
│  └─ None                           -> warning, return            (макрос положить некуда)
├─ macros = dict(dag.user_defined_macros or {})    # копия: DAG не трогаем до конца вычислений
│  └─ MACRO есть и это не наш ol_macro -> warning, return          (§8, инвариант 2)
├─ jar = os.environ.get("OPENLINEAGE_JAR", "")
│  ├─ пусто либо без схемы/пути      -> warning, return            (правила — §5.2)
│  └─ not jar_available(jar)         -> warning(dag_id, task_id), return
├─ ol = ol_conf_template(forced is True)           # значения — строки с Jinja, все три через MACRO
├─ merged = {**ol, **cur_conf}                     # DAG-conf побеждает
├─ jars = merge_jars(getattr(task, attrs.jars), cur_conf.get("spark.jars"), jar)   # см. ниже
├─ macros[MACRO] = ol_macro ; dag.user_defined_macros = macros   # (1) сначала макрос
├─ setattr(task, attrs.jars, jars)                 # (2) потом jar
└─ setattr(task, attrs.conf, merged)               # (3) и только потом листенер
```

Порядок гейтов в этой схеме **нормативен**. В частности, `forced is False` проверяется **раньше**
`cur_conf`: DAG, который и выключил тумблер, и сам задал `spark.extraListeners`, получает тихий
`return` без warning'а — форс-выключение это осознанное решение, и объяснять его нечем. Строка
таблицы §5.3 про чужой листенер и инвариант 9 сформулированы под этот порядок.

`os.environ.get(..., "")`, а не индексация: незаданная `OPENLINEAGE_JAR` обязана попасть в ветку
«пусто → warning», а не в `KeyError`, который ушёл бы в общий `except Exception` и логировался бы
как непредвиденная ошибка политики на каждую таску каждого парса.

**Проверка `MACRO` — по идентичности, а не по наличию.** Политика зовётся по разу на каждую таску,
и все таски одного файла обрабатываются в одном процессе против одного и того же объекта `dag`:
на второй таске `dag.user_defined_macros[MACRO]` уже содержит то, что положила первая. Правило
«ключ занят → чужое → отказ» выключило бы лайнидж всем таскам, кроме первой, — в том числе обеим
таскам стендового `spark_etl_dag`. Поэтому условие отказа — `MACRO in macros and macros[MACRO] is not
ol_macro`: чужим считается только объект, который не является нашей функцией.

**Динамически размапленные таски (`SparkSubmitOperator.partial(...).expand(...)`) не поддерживаются
сознательно.** В `dag.tasks` такая таска лежит как `MappedOperator`, а не как экземпляр
`SparkSubmitOperator`, её conf/jars живут в `partial_kwargs`, и адресация через `operator_attrs` на ней
не работает. Гейт `isinstance` её отсеивает — но молча, а молчаливый пропуск запрещён инвариантом 9.
Поэтому гейт двухступенчатый: экземпляр `SparkSubmitOperator` → обрабатываем; объект, у которого
`operator_class` (или `task_type`) указывает на `SparkSubmitOperator`, но сам он не его экземпляр →
**warning «динамический маппинг не поддерживается»** и `return`; всё остальное → тихий `return`.
Импорт `MappedOperator` для этой проверки не требуется, поэтому ветка безопасна и в случае, если
`MappedOperator` до политики не доходит вовсе (факт не перепроверялся, §11).

**Имена атрибутов резолвятся, а не зашиваются — это требование §1.1.** В провайдере 4.1.1 conf лежит
в `task._conf`, в 4.10.0 — в `task.conf`; `template_fields` в этих версиях перечисляет соответственно
`"_conf"`/`"_jars"` и `"conf"`/`"jars"`. Присваивание по фиксированному имени не падает на «чужой»
версии — оно создаёт новый атрибут, которого не читает ни `_get_hook`, ни рендер шаблонов. Отказ
полностью молчаливый: DAG работает, лайниджа нет, в логе пусто.

```python
_ATTR_CANDIDATES = {"conf": ("conf", "_conf"), "jars": ("jars", "_jars")}

def operator_attrs(task: object) -> SimpleNamespace | None:
    """Имена атрибутов conf/jars у этого оператора, или None если раскладка незнакома."""
    fields = set(getattr(task, "template_fields", ()) or ())      # с экземпляра, как читает Airflow
    resolved = {}
    for role, candidates in _ATTR_CANDIDATES.items():
        for name in candidates:
            if name in fields and hasattr(task, name):
                resolved[role] = name
                break
        else:
            return None
    return SimpleNamespace(**resolved)
```

`template_fields` читается **с экземпляра**, а не с типа: при рендере Airflow обращается к
`self.template_fields` (обычный фолбэк на класс), и оператор, переопределивший список на экземпляре,
иначе проверялся бы политикой по одному списку, а рендерился по другому.

Два условия проверяются вместе намеренно. `template_fields` отвечает на вопрос «что будет отрендерено»
— без него мы могли бы записать в атрибут, который никогда не пройдёт через Jinja, и в conf уехала бы
сырая строка `{{ ... }}`. `hasattr` отвечает на вопрос «что реально читает `_get_hook`» — без него
достаточно было бы опечатки в `template_fields` будущей версии, чтобы мы создали атрибут-пустышку.
Совпадение обоих признаков — единственный надёжный признак того, что запись сработает.

Незнакомая раскладка (ни один кандидат не проходит оба теста) — это **не** повод угадывать: политика
пишет warning и не трогает таску. Отсутствие лайниджа заметно и исправимо; лайнидж, записанный не в
тот атрибут, неотличим от исправной работы.

**Слияние.** Порядок сохраняется из текущей реализации: явный `conf` в DAG'е побеждает — осознанные
переопределения не затираются.

**Чужой `spark.extraListeners` в conf таски — полный отказ от инъекции, а не слияние.** Из пяти
инжектируемых ключей (§5.4) через макрос идут три, а `spark.openlineage.transport.type` подставляется
литералом. Значит DAG, который сам включил лайнидж старым способом
(`conf={"spark.extraListeners": "io.openlineage.spark.agent.OpenLineageSparkListener"}` — ровно так это
сделано в `kyuubi/config/kyuubi-defaults.conf` и в jupyter), при отсутствующей, битой или выключенной
Variable получил бы **свой** листенер, **наш** `transport.type=http` и **наш** `transport.url`,
отрендеренный в пустую строку: конструктор HTTP-транспорта падает, за ним конструктор листенера, за
ним `SparkContext`. Это ровно тот отказ, который §5.1 объявляет невозможным. Поэтому **чужой**
`spark.extraListeners` в conf таски — сигнал «DAG управляет лайниджем сам»: политика пишет warning
и не трогает ни conf, ни jars. Строка в §5.3, тест в §9, инвариант 9 в §8.

**«Чужой» определяется по значению, а не по наличию ключа** — симметрично проверке макроса. Своим
считается значение, совпадающее с одним из двух вариантов нашего шаблона:

```python
_OUR_LISTENERS = frozenset(
    ol_conf_template(f)["spark.extraListeners"] for f in (True, False)
)


def foreign_listener(cur_conf: dict[str, object]) -> bool:
    """Задан ли в conf таски не наш ``spark.extraListeners``.

    :param cur_conf: conf таски, каким он был до политики.
    :return: True, если ключ задан непустым значением и это не шаблон политики.
    """
    value = cur_conf.get("spark.extraListeners")
    return bool(value) and value not in _OUR_LISTENERS
```

Без этой проверки повторный прогон политики по **той же** таске (тот же объект обрабатывается
дважды в одном процессе) видел бы собственный шаблон, оставленный первым прогоном, читал бы его как
чужой листенер и писал бы вводящий в заблуждение warning «DAG управляет лайниджем сам» — тот же
класс ошибки, что «ключ занят → чужое» у макроса. С ней политика **идемпотентна**: второй прогон
даёт тот же conf (`{**ol, **cur_conf}`, где `cur_conf` уже содержит те же значения), тот же список
jar'ов (`merge_jars` дедуплицирует) и тот же макрос (проверка по идентичности). Тест §9 «повторный
прогон не дублирует jar» проверяет именно это, а не срабатывание гейта.

**jar едет через атрибут jars оператора, а не через `spark.jars`.** Ревизия 1 дописывала jar в
`spark.jars` внутри conf. Это скрытая ловушка: у оператора есть собственный параметр `jars=`, который уходит в
`--jars`, а `SparkSubmitArguments.loadEnvironmentArguments` берёт `Option(jars).orElse(sparkProperties
.get(JARS.key))` — то есть при заданном `jars=` в DAG'е значение из `--conf spark.jars` перестаёт быть
источником, и джоба получает листенер без jar'а, то есть `ClassNotFoundException` — ровно тот отказ,
против которого написан зонд. Политика обязана писать в **тот же канал**, что и DAG.

**`merge_jars` собирает три источника, а не два.** Сигнатура —
`merge_jars(current: str | None, conf_jars: object, jar: str) -> str`: она разбивает по запятой
значение атрибута jars, значение `conf["spark.jars"]` из DAG'а и добавляет наш jar, отбрасывая
пустые элементы и дубликаты с сохранением порядка (jars → conf → наш). `None` и не-строка на входе
дают пустой вклад; результат из одного элемента — просто наш jar.

Третий источник добавлен потому, что смена канала иначе **отбирает у DAG'а его собственные jar'ы**.
DAG, перечисливший их в `conf["spark.jars"]` и не задавший `jars=` (текущая реализация обслуживает
такой вход корректно — она дописывает наш jar в тот же ключ), после смены канала получил бы от нас
`--jars` с одним нашим jar'ом, а по факту из §2 явный `--jars` вытесняет `spark.jars` как источник
значения — то есть jar'ы DAG'а перестали бы доезжать. Забирая их элементы в `--jars`, политика
сохраняет намерение DAG'а при любом исходе открытого вопроса про `OptionAssigner`/`mergeFn` (§11):
ключ `conf["spark.jars"]` при этом **не переписывается и не удаляется** — он остаётся ровно таким,
каким его задал DAG (инвариант 4). Дублирование одного и того же пути в двух каналах безвредно:
источником Spark берёт один из них, а не оба сразу.

**Порядок трёх мутаций значим и зафиксирован.** Сначала макрос в `user_defined_macros`, затем jars,
затем conf. Обрыв на любом шаге оставляет таску максимум с лишним макросом и лишним jar'ом на
classpath, но без листенера — то есть без лайниджа, что безопасно. Обратный порядок оставил бы
листенер без jar'а (падающая джоба) или шаблон без макроса (падающий рендер). Все значения
вычисляются в локальных переменных до первой мутации (инвариант 3), `user_defined_macros`
модифицируется через копию и присваивается целиком.

**Чтение тумблера.** `lineage_forced(task)` возвращает `True` / `False` / `None`: `task.params`, затем
`task.dag.params`, если `task.dag` не `None`. `None` на уровне означает «уровень не высказался» и
передаёт решение ниже; на решение влияют только `True` и `False`.

**Молчаливое «не высказался» и «не высказался + warning» — разные ветки, и путать их нельзя.**
Уровень молчит без warning'а в двух случаях: ключа `openlineage` в `params` **нет** вовсе и ключ есть,
но его значение — `None`. Первое — нормальное состояние любого DAG'а, не использующего тумблер
(на стенде это оба: `spark_pi_dag` и `spark_etl_dag`), второе — объявленный нейтральный `Param`
(§5.3). Warning пишется, только когда ключ **присутствует** и его значение не `bool` и не `None`
(`"yes"`, `1`, `[]`) либо когда чтение `params` бросило исключение. Иначе политика писала бы
предупреждение на каждую таску каждого парса всех обычных DAG'ов — раз в 30 секунд на файл
бессрочно, против цели 4 §1.

Обращение к `params` идёт под собственным `try/except`: `ParamsDict.__getitem__` **валидирует**
`Param` и бросает `ParamValidationError`, которую `Mapping.get` не ловит — она ловит только
`KeyError`. Поэтому проверка «ключ есть?» делается по `in`, а чтение значения — отдельной
операцией под `try/except`.

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
Сидится идемпотентно при старте контейнера (§7). Ключи сверх перечисленных игнорируются —
**с единственным исключением: `auth`** (см. ниже и §5.4), который распознаётся именно для того,
чтобы отказать явно, а не молча.

**Все три ключа обязательны для включения, и значения валидируются, а не только проверяются на
непустоту.** Лайнидж включается только если истинно `enabled` (либо стоит форс из DAG'а) **и** оба
значения проходят проверку:

| Поле | Годное значение |
| --- | --- |
| `url` | `isinstance(v, str)`, после `.strip()` непусто и начинается с `http://` либо `https://` |
| `namespace` | `isinstance(v, str)`, после `.strip()` непусто |

В conf уезжает именно очищенное (`.strip()`) значение. Проверка **не** пытается быть валидатором URL
целиком: её задача — отсечь три реальных входа, которые `bool()` пропускает, а транспорт не переживает:
число вместо строки (`"url": 5000` — валидный JSON), строку без схемы (`"marquez:5000"`) и строку
из одних пробелов (типовая правка в UI). Причина строгости — пустое или негодное значение даёт не
«дефолт», а отказ: `spark.openlineage.transport.url` без схемы роняет построение HTTP-транспорта
внутри листенера, а падение конструктора листенера роняет `SparkContext`, то есть джобу; пустой
`spark.openlineage.namespace` уезжает в событие и отвергается Marquez по charset-валидации namespace.
Неполный конфиг обязан деградировать в «лайниджа нет», а не в «джобы нет».

**Значение читается как сырая строка и разбирается в Python.** Хранимое значение — текст JSON,
как его вводят в поле `Val` в UI. Разбор — `json.loads` под `try/except` плюс проверка
`isinstance(parsed, dict)`: без неё валидный, но не-объектный JSON (строка, число, список) прошёл бы
`json.loads` и упал бы на `.get` уже внутри рендера. Это не теоретический случай — именно так
выглядит результат ошибочного сидинга через `--json` (§7).

### 5.2 Переменные окружения

| Переменная | Роль | Читается |
| --- | --- | --- |
| `OPENLINEAGE_JAR` | **полный HDFS-URI** jar'а; обязан совпадать с тем, что заливает `scripts/seed-openlineage-jar.bat` | на парсе, каждый раз |
| `OPENLINEAGE_URL`, `OPENLINEAGE_NAMESPACE` | значения для **первичного сидинга** Variable | один раз при бутстрапе |

**Форма значения задана и проверяется — это не «просто путь».** Фактическое значение в репозитории
(`env_example:32`, `README.md:221`) — `hdfs://namenode:9000/opt/openlineage/openlineage-spark_2.13-1.46.0.jar`,
то есть URI со схемой, authority и путём. Контракт политики:

| Составляющая | Как используется |
| --- | --- |
| схема | обязана быть непустой; пустая (`/opt/x.jar`) — warning «`OPENLINEAGE_JAR` без схемы» и отказ от инъекции. Значение без схемы `spark-submit` трактует в `--jars` как **локальный** файл сабмит-хоста, и джоба падает на локализации — то есть без этой проверки зонд подтвердил бы наличие файла, а джоба всё равно бы упала |
| путь (`urlparse(jar).path`) | подставляется в WebHDFS-URL как `<endpoint>/webhdfs/v1<path>?op=GETFILESTATUS`. Пустой путь — warning и отказ |
| authority (`host:port`) | **игнорируется полностью.** В HDFS-URI это RPC-хост и RPC-порт (`hdfs://<HOST>:<RPC_PORT>/<PATH>`), а WebHDFS слушает HTTP-порт; эндпоинты берёт резолвер §6 из конфигов кластера. Расхождение authority с `fs.defaultFS` **не** является поводом отказать: кластер один, а конфиги — единственный источник эндпоинтов. Проверять это расхождение реализация не должна |
| значение целиком | уезжает в `--jars` как есть и служит **ключом мемо** зонда (§6.1) — именно исходная строка, не нормализованный путь: она же определяет и путь зонда, и то, что попадёт в jars |

Основание для формы WebHDFS-URL — доки Hadoop: FileSystem-URI `hdfs://<HOST>:<RPC_PORT>/<PATH>`
соответствует HTTP-URL `http://<HOST>:<HTTP_PORT>/webhdfs/v1/<PATH>?op=...`; префикс `/webhdfs/v1`
вставляется в путь.

`OPENLINEAGE_JAR` остаётся в окружении намеренно: путь к jar — свойство деплоя, и он нужен на парсе,
где Jinja ещё не выполнена. Фолбэка «нет Variable → взять url/namespace из ENV» **нет**: источник
конфигурации ровно один, зашитых дефолтов (`http://marquez:5000`, `hadoop-cluster`) в коде не
остаётся.

**Разовость сидинга обязана быть написана в `env_example` прямым текстом.** `OPENLINEAGE_URL` и
`OPENLINEAGE_NAMESPACE` живут в общем блоке `x-versions` и раздаются всем сервисам, поэтому их правка
выглядит как правка конфигурации лайниджа — но после первого старта она не меняет ничего: значение
уже в Variable. Классические грабли «поправил `.env`, перезапустил, ничего не изменилось». Явный
`OPENLINEAGE_CONFIG_RESEED=true` (§7) — единственный способ перезаписать Variable из окружения.

**`AIRFLOW_VAR_OPENLINEAGE_CONFIG` перекрывает метастор.** `EnvironmentVariablesBackend` стоит первым
в цепочке секрет-бэкендов, поэтому переменная окружения с таким именем сделает правку в UI
недействующей. Это одновременно грабли и аварийный рычаг (включить/выключить лайнидж без БД) —
документируется в `README.md`, в `docker-compose.yml` не задаётся.

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

| `task.params` | `dag.params` | Variable | Итог | Где решается |
| --- | --- | --- | --- | --- |
| `False` | — | — | выкл | парс |
| `True` | — | `url` и `namespace` годны (§5.1) | вкл | парс + рендер |
| `True` | — | нет Variable / битый JSON / не-объект / негодный `url` или `namespace` | **выкл + warning** | рендер |
| нет | `False` | — | выкл | парс |
| нет | `True` | как в двух строках форса выше | так же | парс + рендер |
| нет | нет | `enabled: true`, `url` и `namespace` годны | вкл | рендер |
| нет | нет | `enabled: false`, при любых `url`/`namespace` | выкл, **молча** | рендер |
| нет | нет | Variable не задана / недоступна / битый JSON / не JSON-объект | **выкл + warning** (пишет `_cfg`) | рендер |
| нет | нет | объект есть, но `enabled` отсутствует либо не `bool` (в том числе **пустой объект `{}`**) | **выкл + warning** | рендер |
| нет | нет | `enabled: true`, но негодный `url` или `namespace` | **выкл + warning** с именем поля | рендер |
| не-`bool` и не `None` | не-`bool` и не `None` | — | уровень игнорируется + warning, решение уходит ниже | парс |
| `None` | `None` | как в строках «нет / нет» | так же, **без** warning'а: `None` — объявленное «не высказался» (§4.2) | парс |
| не `False` | не `False` | любая | **не трогаем таску + warning**, если в conf таски задан **чужой** `spark.extraListeners` (§4.2) | парс |

**Строки «выкл + warning» разделены намеренно.** Молча выключаться вправе **ровно один** случай:
`enabled` присутствует и равен `False`. Это единственная конфигурация, о которой известно, что
выключение осознанное. Отсутствие Variable, битый JSON, не-объект и отсутствующий или не-булев
`enabled` — все они дают тот же наблюдаемый результат «лайниджа нет», но ни один из них не является
чьим-то решением, поэтому каждый пишет warning со своей причиной. Ревизия 3 в первой редакции
сваливала их в одну строку с honest-off и в коде уходила в `return ""` до всякого логирования —
то есть воспроизводила блокер 1 ревизии 2 в новом месте. `enabled: true` при негодном `url`/`namespace` —
**самопротиворечивая** конфигурация и логируется так же, как форс: warning с именем поля, которого не
хватило. Это не редкий случай: сидинг из §7 всегда пишет `"enabled": True` и подставляет
`os.environ.get("OPENLINEAGE_URL", "")`, то есть при незаданной переменной окружения штатно создаёт
Variable с включённым лайниджем и пустым адресом. Без этой строки такой конфиг был бы внешне
неотличим от честного `enabled: false` — то есть воспроизводил бы «молча выключенный навсегда
лайнидж», закрытый ревизией 2 как блокер 1.

**Пустой объект `{}` — это недонастроенный конфиг, а не выключенный.** Он проходит `json.loads`,
проходит `isinstance(dict)` и попадает в строку «объект есть, но `enabled` отсутствует» со своим
warning'ом. Отдельной оговорки строка требует потому, что `{}` — типовой результат ручной правки в UI
и одновременно ложный «сентинел ошибки», если реализация вернёт из `_cfg` пустой dict при отказе:
тогда легальный `{}` и четыре причины отказа станут неразличимы, и лайнидж выключится молча. Поэтому
`_cfg` возвращает `None` при отказе и dict при успехе — §5.4.

**Последняя строка не отменяет форс-выключения.** Она сформулирована как «не `False`» намеренно:
порядок гейтов §4.2 ставит `forced is False` **выше** чтения conf, поэтому DAG, который выключил
тумблер и при этом сам задал `spark.extraListeners`, уходит тихим `return`'ом без warning'а. Это
согласовано с инвариантом 9, который разрешает молчание при форс-выключении.

**Форс не обходит зонд jar.** `params={"openlineage": True}` при недоступном jar лайнидж не включает —
иначе вернулся бы `ClassNotFoundException`, ради которого зонд и писался (коммит `9b6775b`). В лог
уходит warning с `dag_id` и `task_id`, чтобы расхождение «включил, а лайниджа нет» находилось грепом.

**Форс не обходит и полноту конфига.** Ревизия 1 подставляла при форсе литеральный листенер, оставляя
`url` шаблоном, — то есть форс без Variable давал включённый листенер с пустым `transport.url`
и падающую джобу. Форс переопределяет **только** `enabled`; требование **годных** `url` и `namespace`
(§5.1 — тип, `strip`, схема) действует всегда. Warning пишется на воркере, в лог таски, где его
и ищут.

**Тумблер виден в форме «Trigger DAG w/ config», но правка в ней ни на что не влияет:** инъекция уже
произошла на парсе, а `params` из формы попадают только в контекст выполнения.

**Объявление ключа — это уже решение, а не подпись к нему.** `Param` резолвится в свой дефолт, поэтому
`Param(False, ...)` — не «документация переключателя», а форс-выключение всего DAG'а, и `Param(True, ...)`
— постоянный форс-включение, отменяющий `enabled` из Variable. Первая редакция ревизии 3 рекомендовала
именно `Param(False, ...)` «чтобы не показывать неработающий переключатель» — то есть предлагала автору
DAG'а молча потерять лайнидж. Рекомендации ровно три, в порядке предпочтения:

| Что нужно автору DAG'а | Как объявлять |
| --- | --- |
| решение из Variable, тумблер не нужен (обычный случай, оба DAG'а стенда) | **не объявлять ключ вовсе** — отсутствие ключа нейтрально и молчаливо (§4.2) |
| решение из Variable, но ключ виден и описан в форме запуска | `Param(None, type=["null", "boolean"], description=...)` — `None` нейтрален и warning'а не пишет |
| DAG всегда с лайниджем или всегда без него | `Param(True, ...)` / `Param(False, ...)` — это осознанный форс, а не подпись |

```python
# нейтрально: ключ виден в форме и описан, решение остаётся за Variable
params={"openlineage": Param(None, type=["null", "boolean"],
                             description="Решается на парсе DAG; правка в форме запуска не действует")}
```

`type=["null", "boolean"]` — документированная форма объявления необязательного параметра (доки
Airflow, раздел Params: «For optional fields that allow empty input, the type must explicitly include
`null`»); по тегам 2.6.3 и 2.10.2 это **не** перепроверялось, риск — в §11. Если валидатор `Param` в
целевой версии такую форму не принимает, остаётся первая строка таблицы: не объявлять ключ.

То же — абзацем в `README.md`, вместе с оговоркой, что объявление с дефолтом `True`/`False` есть форс.

### 5.4 Инжектируемые ключи

```python
MACRO = "__openlineage_v1"      # имя версионировано: см. «Коллизия имени» ниже
LISTENER = "io.openlineage.spark.agent.OpenLineageSparkListener"


def ol_conf_template(forced_on: bool) -> dict[str, str]:
    """Значения conf: три из пяти — вызовы одного макроса, решение целиком в Python.

    :param forced_on: True, если DAG форсировал включение. Третьего состояния у параметра нет:
        форс-выключение отсечено гейтом §4.2 и до этой функции не доходит, поэтому «нет форса»
        и «форс выключен» здесь не путаются — вызывающий передаёт ``forced is True``.
    :return: словарь conf-ключей, где три значения — Jinja-вызовы макроса ``MACRO``.
    """
    f = "true" if forced_on else "none"           # в макрос уедет True либо None
    return {
        "spark.extraListeners": f"{{{{ {MACRO}('listener', {f}) }}}}",
        "spark.openlineage.transport.type": "http",
        "spark.openlineage.transport.url": f"{{{{ {MACRO}('url', {f}) }}}}",
        "spark.openlineage.namespace": f"{{{{ {MACRO}('namespace', {f}) }}}}",
        "spark.openlineage.columnLineage.datasetLineageEnabled": "true",
    }
```

Ни одной логической операции в самом шаблоне: Jinja только зовёт функцию и подставляет её строку.
Ревизия 1 держала `if ... else ''` внутри шаблона и литерал при форсе — обе конструкции убраны,
потому что именно они разъезжались между ветками (форс включал листенер, не проверив url).

Пустой `spark.extraListeners` безопасен — обосновано в бриф-таблице по исходникам Spark 3.5.2.
Пустые `transport.url` / `namespace` при пустом листенере не читаются никем.

Макрос, который политика кладёт в `dag.user_defined_macros` на парсе:

```python
_WARN_TTL_SEC = 300.0
_now = time.monotonic          # единственный источник времени модуля; тесты подменяют его

_warned: dict[str, float] = {}


def _warn_once(key: str, msg: str, *args: object) -> None:
    """Пишет warning не чаще одного раза в ``_WARN_TTL_SEC`` по ключу дедупликации.

    :param key: ключ дедупликации.
    :param msg: шаблон сообщения для logging.
    :param args: аргументы шаблона; значения из Variable сюда не передаются (инвариант 5).
    :return: None.
    """
    now = _now()
    last = _warned.get(key)
    if last is not None and now - last < _WARN_TTL_SEC:
        return
    _warned[key] = now
    _log.warning(msg, *args)


def _clean(value: object, *, require_scheme: bool = False) -> str:
    """Годное значение поля конфига либо пустая строка (правила — §5.1).

    :param value: сырое значение из Variable.
    :param require_scheme: требовать префикс ``http://`` или ``https://``.
    :return: значение без окружающих пробелов, либо "" если оно негодно.
    """
    if not isinstance(value, str):
        return ""
    cleaned = value.strip()
    if require_scheme and not cleaned.startswith(("http://", "https://")):
        return ""
    return cleaned


@functools.lru_cache(maxsize=1)
def _cfg() -> dict[str, object] | None:
    """Конфиг OL из Airflow Variable. Никогда не бросает: при любой ошибке — ``None``.

    Мемо на процесс: значение читается тремя вызовами макроса за один рендер, а процесс запуска
    таски на воркере живёт одну таску — внутри него устаревание невозможно. Гарантия ограничена
    именно этим процессом: на исполнителе с переиспользуемыми процессами мемо становится кэшем
    без TTL (риск в §11).

    :return: разобранный объект конфига (в том числе пустой ``{}``) либо ``None``, если конфиг
        прочитать не удалось; причина в этом случае уже записана в лог.
    """
    from airflow.models import Variable
    try:
        raw = Variable.get("openlineage_config", default_var=None)
    except Exception:
        _warn_once("no-var", "OpenLineage выключен: Variable openlineage_config недоступна")
        return None
    if not raw:
        _warn_once("no-var", "OpenLineage выключен: Variable openlineage_config не задана")
        return None
    try:
        parsed = json.loads(raw)
    except Exception:
        _warn_once("bad-json", "OpenLineage выключен: Variable openlineage_config — не разбираемый JSON")
        return None
    if not isinstance(parsed, dict):
        _warn_once("not-object", "OpenLineage выключен: Variable openlineage_config — не JSON-объект")
        return None
    if "auth" in parsed:
        _warn_once("auth", "OpenLineage: ключ 'auth' в Variable не поддерживается и не подставляется")
    return parsed


def ol_macro(field: str, forced: bool | None = None) -> str:
    """Единственная точка решения «включён ли лайнидж» и единственный источник значений.

    :param field: "listener", "url" либо "namespace".
    :param forced: True, если DAG форсировал включение; None — «форса нет».
    :return: значение поля либо "" если лайнидж выключен или конфиг негоден.
    """
    cfg = _cfg()
    if cfg is None:                               # причина уже записана внутри _cfg
        return ""
    enabled = cfg.get("enabled")
    if not (forced is True or enabled is True):
        # Молча — только когда выключение осознанное: enabled ровно False.
        # Всякая иная причина (отсутствующий или не-булев enabled, в том числе пустой объект)
        # пишет свой warning здесь; отказы чтения уже написали свой внутри _cfg.
        if enabled is not False:
            _warn_once("bad-enabled",
                       "OpenLineage выключен: в Variable openlineage_config поле enabled "
                       "отсутствует или не является булевым")
        return ""
    url = _clean(cfg.get("url"), require_scheme=True)
    namespace = _clean(cfg.get("namespace"))
    if not url or not namespace:
        _warn_once("bad-config",
                   "OpenLineage не включён: в Variable openlineage_config негодно поле %s",
                   "url" if not url else "namespace")
        return ""
    return {"listener": LISTENER, "url": url, "namespace": namespace}[field]
```

**Сентинел отказа и легальное значение разведены намеренно — это не стилистика.** Первая редакция
ревизии 3 возвращала из `_cfg` пустой dict и при отказе, и при успешном разборе значения `{}`, а
`ol_macro` подавлял повторный warning условием `if cfg and ...`, то есть по **истинности** словаря.
Валидная Variable со значением `{}` (типовой результат ручной правки в UI) проходила по успешному
пути `_cfg` — там warning'а нет — и по ложному `if cfg` в `ol_macro` — там его тоже нет. Итог:
«лайниджа нет, в логе пусто», ровно тот класс отказа, который §5.3 разрешает **только** честному
`enabled: false`. `None` как единственный признак отказа закрывает ветку целиком: `ol_macro`
различает «уже предупредили» и «объект прочитан» по типу, а не по истинности.

`url` и `namespace` вычисляются **после** гейта `enabled`, а не до: при честном `enabled: false`
негодные значения полей не должны ни читаться, ни логироваться — выключенный конфиг не обязан быть
полным.

Почему макрос, а не `{{ var.json.openlineage_config.url }}`: `Variable.get(deserialize_json=True)`
делает `json.loads` без обработки, поэтому **битый JSON в существующей переменной уронил бы рендер и
таску**. `default_var` от этого не защищает — он покрывает только отсутствие переменной. Макрос
переносит `json.loads` в Python под `try/except` и делает шаблон невозможным к падению.

**Коллизия имени макроса.** `user_defined_macros` — общее пространство имён DAG'а. Правило ревизии 1
«кладём идемпотентно, не затирая чужие» на неуникальном имени `ol_cfg` даёт худший исход: чужая
функция остаётся, а наши шаблоны зовут её — с произвольным результатом или падением рендера, то есть
пробоем инварианта 2. Поэтому имя версионировано (`__openlineage_v1`), а при обнаружении под ним
**чужого** объекта политика **не инжектит OL вовсе** и пишет warning: занятое имя означает, что DAG
сознательно вмешался. Чужим считается объект, который не является нашей функцией `ol_macro`
(проверка по идентичности, §4.2), — иначе вторая и последующие таски того же файла считали бы
чужим то, что положила первая.

**Самопротиворечивый конфиг не выключается молча.** Когда лайнидж запрошен — форсом из DAG'а либо
`enabled: true` в Variable, — но `url` или `namespace` не проходят проверку §5.1, `ol_macro` пишет
warning с **именем** негодного поля. Молча возвращает `""` только честно выключенный конфиг
(`enabled` не `true` и форса нет). Лог макроса — лог таски на воркере.

**Кратность warning'ов задана явно.** Макрос зовётся трижды за один рендер, поэтому сообщения идут
через `_warn_once`: один warning на ключ дедупликации не чаще раза в `_WARN_TTL_SEC` (300 с).
Процесс запуска таски живёт одну таску, значит на практике это один warning на запуск таски — не три.
TTL здесь по той же причине, что и у мемо зонда (§6.1): дедупликация модульная, и без TTL на
исполнителе с переиспользуемыми процессами первый же warning погасил бы ключ навсегда, а все
последующие таски того же процесса выключали бы лайнидж молча — то есть дедупликация вернула бы
диагностическую половину того самого отказа, против которого написана вся §5.3. Три модульных
кэша политики (`_warned`, мемо зонда, `_cfg`) теперь ведут себя одинаково: TTL есть у первых двух,
у `_cfg` он вынесен решением в §11 вместе с вопросом об исполнителе облака.

Секреты в conf не попадают: политика инжектит только `transport.type` и `transport.url`. Ключ `auth`
— единственное исключение из правила «лишние ключи игнорируются» (§5.1): он распознаётся в `_cfg`,
пишется warning «не поддерживается», подстановки не происходит. Место проверки выбрано так, чтобы
warning был ровно один: `_cfg` мемоизирован, `ol_macro` — нет. Причина отказа: любое значение из conf
таски уезжает в командную строку `spark-submit` и видно в `ps` и в YARN. Значения полей в лог не
пишутся никогда — только их имена.

## 6. Зонд jar в HDFS

Резолв эндпоинтов повторяет `SparkAPI/app/core/hadoop_api/hdfs_api.py::HdfsApi._resolve_urls`:

1. `dfs.http.policy == "HTTPS_ONLY"` → схема `https` и ключ `dfs.namenode.https-address`,
   иначе `http` и `dfs.namenode.http-address`.
2. HA: для каждого `dfs.nameservices` → `dfs.ha.namenodes.<ns>` → `<addr_key>.<ns>.<nn>`.
3. Не HA: одиночный `<addr_key>`.
4. Фолбэк: хост из `fs.defaultFS` (`core-site.xml`) + `9870` / `9871`.

Полный резолв (HA + `HTTPS_ONLY`) оставлен сознательно, хотя на этом стенде всегда срабатывает пункт 4
(§1). Кэш разбора XML по mtime — снят (§4.1).

Запрос — `GETFILESTATUS` по эндпоинтам подряд до первого **давшего осмысленный ответ**. URL строится
как `<endpoint>/webhdfs/v1<path>?op=GETFILESTATUS`, где `<path>` — путь из `OPENLINEAGE_JAR`, а
`<endpoint>` — резолвнутая пара схема+`host:port` (правила разбора значения переменной — §5.2):

| Ответ эндпоинта | Что делает зонд |
| --- | --- |
| `200` | `True`, перебор прекращается |
| `404` | `False` без warning (штатное «jar не залит»), перебор прекращается |
| `403` и в теле `RemoteException.exception == "StandbyException"` | **следующий эндпоинт**, warning не пишется. Если он был последним — `False` с warning «все NameNode ответили standby» |
| сетевая ошибка либо иной код | **следующий эндпоинт**; если он был последним — `False` с warning |

Исход «список кончился» одинаков для обеих не-терминальных строк: `False` **с** warning'ом. Отличие
только в тексте warning'а — «все NameNode ответили standby» против «эндпоинты недоступны», чтобы по
логу было видно, отказал ли кластер или зонд не нашёл активный NameNode. Молчаливым остаётся ровно
один исход — `404`: он означает, что кластер ответил и jar'а действительно нет.

Строка про standby перенесена из оригинала (`hdfs_api.py`, `_is_standby` / `_handle_response`):
standby-NameNode — это не отказ, а «спроси активный». Без этой ветки первый же standby в HA-списке
завершал бы зонд с `False`, то есть выключал бы лайнидж на исправном кластере — ровно тем отказом,
против которого цель 3 и держит полный резолвер. На этом стенде ветка недостижима (пункт 4 фолбэка,
одиночный NN), поэтому проверяется только тестом.

`${env.VAR}` и `${system.prop}` не раскрываются — как и в оригинале; конфиги стенда такой формы
не используют.

### 6.1 Дедлайн и мемоизация — обязательная часть, а не оптимизация

`task_policy` исполняется в `_bag_dag`, то есть **вне** `timeout(dagbag_import_timeout)`: тот
оборачивает только `parse()` внутри `_load_modules_from_file`. Значит зависший сокет **не даёт
исключения**, которое можно было бы погасить, — он молча съедает бюджет
`[core] dag_file_processor_timeout` (дефолт 50 с), по исчерпании которого процесс парсинга файла
убивают целиком и **DAG исчезает из UI**. Инвариант «политика не влияет на успешность парса»
недостижим через `try/except` в принципе: этот отказ временной, а не исключительный.

**Таймаута сокета для этого недостаточно, и это второй отказ по времени, не покрытый ревизией 2.**
`timeout` у `urlopen` — таймаут операций на объекте сокета; разрешение имени делает отдельный
модульный вызов `socket.getaddrinfo`, у которого параметра таймаута нет вовсе (§2). Хост NameNode
берётся из `fs.defaultFS` / `dfs.namenode.*-address` и в docker-сети резолвится встроенным DNS: при
недоступном резолвере `getaddrinfo` блокируется на `timeout`×`attempts` из `resolv.conf`, умноженные
на число суффиксов поиска, и перекрывает любое значение `timeout=2`. Суммарный бюджет, проверяемый
**перед** вызовом, тоже не помогает: он не прерывает вызов, который уже идёт.

Отсюда ограничители — три, и первый из них обязан быть внешним по отношению к сокету:

| Ограничитель | Константа модуля | Значение | Зачем |
| --- | --- | --- | --- |
| **дедлайн одного вызова `jar_available`** | `_PROBE_DEADLINE_SEC` | 5 с | перебор эндпоинтов целиком уходит в демон-поток, вызывающая сторона ждёт `Thread.join(_PROBE_DEADLINE_SEC)` и по истечении возвращает `False`. Это единственная граница, которая покрывает и резолв имени, и произвольно длинный список эндпоинтов при федерации |
| таймаут одного эндпоинта | `_ENDPOINT_TIMEOUT_SEC` | 2 с | обычный `timeout=` у `urlopen`: отсекает медленный, но живой NameNode, не расходуя весь дедлайн |
| мемо результата по `jar_uri` с TTL (`_jar_memo`, `dict` в модуле) | `_MEMO_TTL_SEC` | 300 с | поход в сеть один на парс, а не по одному на каждую таску; TTL не даёт отрицательному результату залипнуть навсегда, если процесс окажется долгоживущим. Ключ — исходное значение `OPENLINEAGE_JAR` (§5.2) |

Все три — **модульные константы**, а не литералы в теле функции, и время берётся через модульный
`_now` (§5.4). Это часть контракта, а не оформление: иначе тест «вызов возвращается не позже
дедлайна» ждал бы настоящие 5 секунд, а тест «по истечении TTL результат переобнаруживается» —
настоящие 300. Тесты подменяют их `monkeypatch.setattr` (§9).

**Почему демон-поток, а не таймаут фреймворка.** `airflow.utils.timeout.timeout` (тот, которым
обёрнут `dagbag_import_timeout`) работает на `SIGALRM`: обработчик сигнала в CPython только
взводит флаг, а исключение поднимается на следующей границе байткода — блокирующий C-вызов
`getaddrinfo` до этой границы может не вернуться. Вдобавок он поднимает `AirflowTaskTimeout`, который
инвариант 1 обязан пробрасывать наружу, — гасить собственный экземпляр пришлось бы по идентичности.
Ожидание с дедлайном на потоке даёт границу **нашего** времени независимо от того, вернётся ли
зависший вызов вообще.

**Поток обязан быть `threading.Thread(daemon=True)`, а не `ThreadPoolExecutor`.** Демонические потоки
на выходе интерпретатора не джойнятся (`threading._shutdown` ждёт только не-демонические), а потоки
пула — джойнятся («are joined before the interpreter exits», §2). Брошенный воркер пула подвесил бы
выход процесса парсинга, то есть заменил бы один отказ по времени другим.

**Мемо пишет ожидающая сторона, а не поток.** `jar_available` кладёт результат в мемо перед тем, как
вернуть его: успех — то, что отдал поток; истечение дедлайна — `False` с warning. Результат
брошенного потока **отбрасывается**: он не имеет права переписать уже опубликованное значение, иначе
две таски одного файла получили бы разные ответы на один и тот же `jar_uri`. Благодаря этому
остальные таски файла не платят ни секунды: N тасок при недостижимом NameNode дают один поход в сеть
и суммарно ≤5 с на файл. При 50-секундном `[core] dag_file_processor_timeout` это запас на порядок.
Отказ NameNode деградирует в «лайниджа нет», не в «DAG'ов нет».

**Остаточный риск назван явно:** брошенный поток продолжает висеть в `getaddrinfo`/`connect` до конца
своей операции. Он ничего не удерживает (результат уже отдан, мемо заполнено) и умирает вместе с
процессом; в долгоживущем процессе он завершится сам. Это единственная часть, которую политика не
контролирует, и она не влияет ни на время парса, ни на его успешность.

Ревизия 1 обещала «1 HTTP GET» в §3, но в §4.2 звала зонд на каждую таску и не имела ни мемо,
ни бюджета — это и есть исправляемое расхождение. Ревизия 2 добавила мемо и «суммарный бюджет на
процесс», но бюджет-аккумулятор проверялся только перед вызовом (не ограничивая идущий) и, будучи
модульным, в долгоживущем процессе выключил бы зонд навсегда после первых 5 секунд. Ревизия 3
заменяет его повызовным дедлайном и даёт мемо TTL: ни один ограничитель больше не залипает.

**Принятый компромисс:** jar дописывается в атрибут jars всегда, когда зонд успешен, даже если Variable
выключает лайнидж — на парсе значение `enabled` неизвестно. Цена — локализация одного jar YARN'ом
на джобу. Альтернатива требует шаблонизировать слияние списков jar'ов в Jinja и читается заметно
хуже.

## 7. Сидинг Variable

В `airflow/scripts/start-airflow.sh`, после `airflow db init`, до старта планировщика:

```sh
# Сборка JSON — питоном, а не конкатенацией строк: значения приходят из окружения
# и обязаны быть корректно заэкранированы.
ol_config_json="$(python - <<'PY'
import json, os
print(json.dumps({
    "enabled": True,
    "url": os.environ.get("OPENLINEAGE_URL", ""),
    "namespace": os.environ.get("OPENLINEAGE_NAMESPACE", ""),
}))
PY
)"

# БЕЗ --json: этот флаг означает «сериализовать значение», а не «значение уже JSON».
if [ "${OPENLINEAGE_CONFIG_RESEED:-false}" = "true" ] \
   || ! airflow variables get openlineage_config >/dev/null 2>&1; then
    airflow variables set openlineage_config "${ol_config_json}"
fi
```

**Почему без `--json` — и почему это блокер, а не стилистика.** `variables_set` вызывает
`Variable.set(key, value, serialize_json=args.json)`, а `Variable.set` при `serialize_json=True`
делает `json.dumps(value, indent=2)`. Значение здесь **уже** строка JSON, поэтому `--json` закодировал
бы её второй раз: в БД легло бы `"{\"enabled\": true, ...}"`. Дальше `json.loads` в макросе вернул бы
`str`, а не `dict`, `.get` бросил бы `AttributeError`, `except` вернул бы `{}` — и лайнидж оказался бы
выключен навсегда и молча, а строка «битый JSON → выкл» в §5.3 это замаскировала бы. Без `--json`
значение хранится ровно так же, как его сохраняет UI (Admin → Variables пишет поле `Val` как есть),
и оба пути записи совпадают. Проверка `isinstance(parsed, dict)` в `_cfg` (§5.4) — вторая линия
обороны на случай ручной правки в UI.

Идемпотентность обязательна: правка через UI обязана переживать рестарт контейнера. `variables get`
при отсутствии ключа завершается ненулевым кодом (`variable_command.py:41-51`). `OPENLINEAGE_CONFIG_RESEED=true` —
единственный способ намеренно перезатереть Variable значениями из окружения (§5.2).

## 8. Инварианты

1. **Политика не бросает.** Любая ошибка — нет конфига, недоступен HDFS, битый XML, не смонтирован
   `HADOOP_CONF_DIR`, неожиданный тип в `params` — гасится, пишется в лог, лайнидж просто не
   включается. Тело `apply_policy` целиком под `except Exception` с `_log.warning(..., exc_info=True)`;
   `task_policy` — делегат без собственной логики (§4.1), поэтому наружу через него ничего не уходит.
   Основание: `dagbag.py:481-485` роняет импорт **всего файла**, то есть баг в политике выключил бы
   все DAG'и разом.
   **Границы инварианта, которых ревизия 1 не видела.** Во-первых, `except Exception` перехватит и
   `AirflowTaskTimeout` (наследник `AirflowException` → `Exception`), поэтому он,
   `AirflowClusterPolicyViolation` и `AirflowClusterPolicySkipDag` пробрасываются явным
   `except passthrough_exceptions(): raise` **до** общего блока — гасить чужой механизм таймаута или
   чужое решение пропустить DAG политика не вправе. `AirflowClusterPolicySkipDag` в 2.6.3 **не
   существует** (появился к 2.10.2, §2), поэтому кортеж собирается **поимённо, каждый класс своим
   `try/except ImportError`**, и **лениво — при первом вызове политики, а не на импорте модуля**
   (решение D5 в §13: `airflow_local_settings` импортируется из `settings.initialize()` до
   `configure_orm()`, и импорт подмодуля Airflow из частично инициализированного пакета на импорте
   политики — риск, которого сборка по первому вызову не имеет):

   ```python
   def passthrough_exceptions() -> tuple[type[BaseException], ...]:
       global _passthrough_cache
       if _passthrough_cache is None:
           collected: tuple[type[BaseException], ...] = ()
           for name in _PASSTHROUGH_NAMES:
               try:
                   collected += (getattr(importlib.import_module("airflow.exceptions"), name),)
               except (ImportError, AttributeError):
                   continue
           _passthrough_cache = collected
       return _passthrough_cache
   ```

   Один общий `from airflow.exceptions import A, B, C` здесь **запрещён**: на 2.6.3 отсутствие
   третьего имени провалило бы весь оператор импорта, кортеж остался бы пустым, и проброс
   `AirflowTaskTimeout` — то, ради чего инвариант и написан, — молча выключился бы именно на той
   среде, где гонка §6.1 наиболее вероятна. Пустой кортеж сам по себе легален (`except (): ...`
   просто никогда не срабатывает), но он обязан быть пустым только тогда, когда в среде нет **ни
   одного** из перечисленных классов. Во-вторых, `try/except` не покрывает отказ по времени:
   политика работает вне `timeout(dagbag_import_timeout)`, и зависший сокет убивает парсинг файла
   целиком. За временную часть отвечает инвариант 7, а не этот.
2. **Ни один шаблон, оставленный политикой, не может уронить рендер.** Все обращения к Variable идут
   через макрос `ol_macro`/`_cfg`, который не бросает; в самом шаблоне нет ни одной логической
   операции — только вызов функции. Прямые `{{ var.json.* }}` в инжектируемом conf запрещены.
   Имя макроса версионировано; если под ним лежит **чужой** объект (проверка по идентичности, а не
   по наличию ключа), политика не инжектит OL вовсе (§5.4). Шаблон не инжектится, если макрос
   положить некуда (`task.dag is None`).
3. **Никакой частичной мутации.** Макрос, jars и conf присваиваются в самом конце, после того
   как все значения вычислены целиком, и именно в этом порядке: обрыв на любом шаге оставляет
   лишний jar без листенера (безопасно), а не листенер без jar'а или шаблон без макроса.
4. **conf из DAG'а побеждает, и ни один заданный DAG'ом jar не теряется.** Порядок слияния —
   OL-ключи первыми, текущий conf таски поверх них. Наш jar **дописывается** к атрибуту jars, а не
   перезаписывает его; туда же дописываются элементы `conf["spark.jars"]`, заданные DAG'ом, потому
   что явный `--jars` вытесняет `spark.jars` как источник значения (§2, §4.2). Сам ключ
   `conf["spark.jars"]` политика не переписывает и не удаляет. Инвариант покрывает три входа: DAG
   задал `jars=`, DAG задал `conf["spark.jars"]`, DAG задал оба.
5. **Секреты не уезжают в argv.** Значения Variable в лог не пишутся — только имена полей;
   `transport.auth` не подставляется, ключ `auth` в Variable даёт warning «не поддерживается» (§5.4).
6. **Ноль обращений к метастору на парсе.** Ни `Variable.get`, ни `BaseHook.get_connection` в коде,
   выполняемом на этапе парсинга. На рендере — ровно одно обращение на запуск таски (мемо, §5.4),
   а не по одному на каждое инжектируемое значение.
7. **Вклад политики в время парса ограничен сверху числом, а не надеждой.** Граница — 5 секунд
   на парс файла, и обеспечивает её **ожидание с дедлайном на демон-потоке**, а не таймаут сокета:
   таймаут сокета не покрывает `getaddrinfo` (§2, §6.1). Ни число `SparkSubmitOperator` в файле
   (мемо), ни число эндпоинтов при федерации (дедлайн на весь перебор), ни зависший DNS не могут
   довести парс до `[core] dag_file_processor_timeout` силами политики. Ограничители не залипают:
   дедлайн повызовный, мемо с TTL.
8. **Ни одного имени атрибута оператора, зашитого в код.** conf и jars адресуются через
   `operator_attrs` (§4.2), которая требует совпадения `template_fields` (читаемого с экземпляра)
   и `hasattr`. Незнакомая раскладка — warning и полный отказ от инъекции. Основание: между 4.1.1 и
   4.10.0 эти атрибуты переименованы из приватных в публичные, и запись по устаревшему имени не
   падает, а создаёт мёртвый атрибут. Проверяемая формулировка: **ни одно чтение и ни одно
   присваивание атрибута оператора не адресует его по фиксированному имени** — только через
   `attrs = operator_attrs(task)`. Конкретные имена (`_conf`/`conf`, `_jars`/`jars`) в спеке
   встречаются, но исключительно как факты версий и как обоснование резолва (§1.1, §2, §4.2, §12) —
   ни одно из этих упоминаний не является указанием писать по фиксированному имени; нормативные
   разделы (поток §4.2, §5.4, §8, §10) называют атрибуты нейтрально — «атрибут conf», «атрибут jars».
   Обратная сторона того же инварианта: код политики не использует ничего, чего нет одновременно в
   `airflow/requirements.txt` и `airflow/requirements_cloud.txt` (§1.1).
9. **Политика не включает лайнидж поверх чужого и не пропускает таску молча.** Если DAG **не**
   форсировал выключение, а в conf таски уже задан **чужой** `spark.extraListeners` (значение,
   не совпадающее с шаблоном политики — `foreign_listener`, §4.2), лайниджем управляет DAG:
   политика не трогает ни conf, ни jars и пишет warning. Если таска выглядит Spark-овой, но
   обработать её нельзя (динамический маппинг, незнакомая раскладка атрибутов, `task.dag is None`,
   занятое имя макроса), политика тоже пишет warning.
   Тихий `return` разрешён ровно в двух случаях: таска не Spark-овая и DAG форсировал выключение.
   Второй случай **сильнее** первого предложения и проверяется раньше него (порядок гейтов §4.2):
   таска с форс-выключением не даёт warning'а, даже если в её conf лежит чужой листенер — объяснять
   там нечего, решение принял DAG. Ровно так же сформулированы последняя строка таблицы §5.3
   («не `False`») и покрывающий её тест §9.

## 9. Тесты

Юнит-тестов в репозитории нет — только `.bat`-смоуки против живого стенда. Заводится минимум,
достаточный для TDD по этой задаче.

Инфраструктура: тесты в `airflow/config/tests/`. `ol_policy.py` и `hadoop_conf.py` не импортируют
Airflow на уровне модуля — импорт оператора и `Variable` внутри функций, как в текущем коде.

`pytest` **не добавляется в `airflow/requirements.txt`**: этот файл описывает рантайм стенда, а не
тестовый контур, и в облачном файле pytest уже есть отдельной строкой (8.3.3). В стендовый образ он
ставится отдельным слоем `airflow/Dockerfile` с пином `7.4.0` из `constraints-2.6.3`. Набор обязан
проходить и на 7.4, и на 8.3: только `assert`, фикстуры и `monkeypatch`; ни возможностей, появившихся
в 8, ни удалённых в 8 (nose-style `setup`/`teardown`, неявные async-тесты без плагина).

Тестовые дубли оператора пишутся **вручную** (два маленьких класса с нужными `template_fields` и
атрибутами), а не импортом настоящего `SparkSubmitOperator`: в каждой отдельно взятой среде доступна
только одна из двух раскладок, а покрыть требуется обе.

Чтобы дубли были применимы, гейт типа и сборка разделены — границы и имена зафиксированы таблицей
§4.1: `apply_policy(task)` держит `try/except` и двухступенчатый гейт из §4.2 (`isinstance` →
«Spark-овая, но не экземпляр» → warning), `inject_openlineage(task)` принимает уже проверенную таску
и делает всё остальное. Тесты раскладок и таблицы истинности бьют по `inject_openlineage`, гейт и
инвариант 1 — по `apply_policy`; обе в `ol_policy.py`, поэтому набор **не импортирует**
`airflow_local_settings.py`. Сам гейт покрывается настоящим оператором той версии, что стоит в среде,
любым не-Spark оператором и дублём с `operator_class`, указывающим на `SparkSubmitOperator`.

**Как тесты находят модули — часть контракта, а не деталь запуска.** Каталог `airflow/config` лежит
на `sys.path` только когда отработал бутстрап Airflow (`prepare_syspath*`), а при голом `python -m
pytest` этого может не случиться до первого импорта. Поэтому в `airflow/config/tests/` заводится
`conftest.py`, который на импорте кладёт родительский каталог в `sys.path`:
`sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))`. Это работает и на 7.4, и на
8.3, не требует `PYTHONPATH` в команде запуска, не требует `__init__.py` и не пишет ничего на диск
(каталог смонтирован `:ro`). Команда запуска — ровно та, что в смоуке ниже, без переменных окружения.

**Сброс модульного состояния между тестами — тоже часть контракта.** Политика держит три куска
состояния на модуль: `_warned` (§5.4), `functools.lru_cache` на `_cfg` (§5.4) и мемо зонда по
`jar_uri` (§6.1). Внутри одного процесса pytest все три переживают границу теста, а `caplog` — нет.
Без сброса набор становится зависимым от порядка и зелёным по случайности: второй тест `_cfg` увидел
бы закэшированный конфиг первого, а второй тест на тот же ключ дедупликации (`bad-enabled` у «объект
без `enabled`» и у `enabled: "yes"`; `bad-config` у пяти случаев негодного `url`) — пустой `caplog`.
Именно те тесты, которые объявлены регрессионной защитой от блокера 1, ломались бы первыми.

Поэтому `ol_policy.py` экспортирует `reset_state()`:

```python
def reset_state() -> None:
    """Сбрасывает всё модульное состояние политики: дедупликацию warning'ов, мемо конфига и зонда.

    Нужна тестам: три кэша живут на модуль и переживают границу теста, а ``caplog`` — нет.

    :return: None.
    """
    _warned.clear()
    _cfg.cache_clear()
    _jar_memo.clear()
```

а `airflow/config/tests/conftest.py` зовёт её из **autouse-фикстуры** — это штатный для pytest способ
привести окружение всех тестов к одному состоянию:

```python
@pytest.fixture(autouse=True)
def _reset_policy_state():
    """Приводит модульное состояние политики к чистому до и после каждого теста."""
    ol_policy.reset_state()
    yield
    ol_policy.reset_state()
```

**Время и пороги подменяются `monkeypatch.setattr`, а не ожиданием.** Модульные `_now`,
`_PROBE_DEADLINE_SEC`, `_ENDPOINT_TIMEOUT_SEC`, `_MEMO_TTL_SEC`, `_WARN_TTL_SEC` (§5.4, §6.1) —
единственные источники времени и порогов; тест подменяет `_now` счётчиком, а пороги — маленькими
значениями, и `monkeypatch` откатывает подмену сам. Собственного «инжектора часов» в сигнатурах
функций не заводится: параметр ради теста в продовом контракте — лишняя степень свободы.

RED-набор:

- таблица истинности тумблера — **все 13 строк** §5.3: четыре строки «выкл + warning» (форс при
  негодном конфиге; отсутствующая/битая/не-объектная Variable; объект без годного `enabled`;
  `enabled: true` при негодном `url`/`namespace`), единственная строка «выкл, молча»
  (`enabled: false`), строка про не-`bool` в `params`, строка про `None` в `params` (нейтрально
  и **без** warning'а) и строка про чужой `spark.extraListeners`;
- **тумблер: отсутствие ключа молчит.** Таска и DAG без ключа `openlineage` в `params` (стендовые
  `spark_pi_dag`/`spark_etl_dag`) → решение уходит в Variable и в лог **не пишется ничего**;
  `params={"openlineage": None}` — то же самое; `params={"openlineage": "yes"}` — warning (§4.2);
- **форс-выключение сильнее гейта чужого листенера**: таска с `params={"openlineage": False}`,
  у которой в conf задан чужой `spark.extraListeners`, не трогается и `caplog` **пуст** (инвариант 9,
  последняя строка §5.3 — «не `False`»);
- форс `True` при недоступном jar не включает лайнидж, пишет warning;
- `resolve_webhdfs_urls`: HA-список, одиночный `http-address`, фолбэк на `fs.defaultFS`, `HTTPS_ONLY`;
- **разбор `OPENLINEAGE_JAR`** (§5.2): URI со схемой → в WebHDFS-URL уезжает `urlparse().path` с
  префиксом `/webhdfs/v1`, а хост берётся из резолвера, **не** из authority URI (эндпоинт с другим
  хостом, чем в `OPENLINEAGE_JAR`, всё равно опрашивается); значение без схемы и значение с пустым
  путём → warning и отказ от инъекции, зонд не вызывается вовсе;
- зонд: `200` → `True`; `404` → `False` без warning; сетевая ошибка → следующий эндпоинт, а на
  последнем — `False` с warning; **`403` + `StandbyException` → следующий эндпоинт, а не отказ**
  (§6); **standby на последнем эндпоинте → `False` с warning, отличимым от «эндпоинты недоступны»**;
  перебор прекращается на первом осмысленном ответе;
- **зонд под дедлайном** (§6.1): второй вызов с тем же `jar_uri` не ходит в сеть (мемо); один вызов
  со списком из многих эндпоинтов, каждый из которых висит, возвращается **не позже дедлайна** и
  отдаёт `False` (эндпоинты подменяются, `_PROBE_DEADLINE_SEC` уменьшается `monkeypatch`'ем до долей
  секунды, а время меряется **реальным** `time.monotonic()`: ожидание идёт внутри
  `Thread.join(_PROBE_DEADLINE_SEC)` по реальным часам, и подменённый `_now` его не видит — тот
  остаётся источником времени только для мемо и TTL `_warn_once`, решение D7 в §13); DAG-файл
  с N `SparkSubmitOperator` при недостижимом NameNode даёт
  ровно один поход в сеть; по истечении `_MEMO_TTL_SEC` (подменённый `_now` шагает вперёд) мемо
  отрицательный результат **переобнаруживается**, а не залипает навсегда; поток, доехавший до
  ответа уже после дедлайна, **не переписывает** опубликованный `False`;
- зонд не использует `ThreadPoolExecutor`: поток, который он создаёт, — демонический
  (проверяется тем, что созданный поток имеет `daemon is True`);
- `_cfg`: отсутствие Variable → `None`; недоступная Variable (исключение) → `None`; битый JSON →
  `None`; **валидный, но не-объектный JSON (строка/число/список) → `None`**; валидный объект → dict;
  **валидный пустой объект `{}` → `{}`, а не `None`** (сентинел отказа и легальное значение
  различимы); ни один случай не бросает; повторный вызов не ходит в БД второй раз (мемо);
- **каждая из четырёх причин отказа `_cfg` пишет ровно один warning со своей причиной** (проверяется
  `caplog`: сообщения различимы между собой), и ни одна из них не оставляет лог пустым — это
  регрессионный тест на блокер 1 ревизии 2 в его втором воплощении;
- **разделение молчания и warning'а по §5.3**: `enabled: false` → `""` и `caplog` **пуст**;
  объект без `enabled` → `""` **и warning**; **валидный пустой объект `{}` → `""` и warning**
  (регрессионный тест на склейку сентинела с легальным значением — с прежним `{}`-сентинелом лог
  оставался пустым); `enabled: "yes"` (не `bool`) → `""` **и warning**. Эти случаи различаются
  только логом, поэтому проверяются именно логом;
- `ol_macro`, валидация значений (§5.1): полный конфиг → значения; негодный `url` → `""` даже при `forced=True` + warning — отдельными
  случаями `""`, `"   "`, `5000` (число), `"marquez:5000"` (без схемы), `None`; негодный `namespace`
  → то же (`""`, `"   "`, не-строка); годные значения с окружающими пробелами попадают в conf
  **обрезанными**;
- **инвариант 5**: Variable с ключом `auth` → warning «не поддерживается», и `auth` не появляется
  ни в одном инжектируемом ключе conf; ни одно значение `url`/`namespace` не встречается в тексте
  ни одной записи лога (проверяется `caplog` по всему набору);
- **инвариант 6, парсовая половина**: `Variable.get` и `BaseHook.get_connection` подменяются
  дублями, которые валят тест при вызове; полный прогон `apply_policy` по обеим раскладкам не
  трогает ни один из них. Обращение допускается только изнутри макроса на рендере;
- **рендер настоящим Airflow'ом** (перенесено из §11 в RED — это чистый unit, стенд не нужен):
  `DAG(...).get_template_env()` с макросом в `user_defined_macros` рендерит все три инжектируемых
  значения в `SandboxedEnvironment` без ошибок и с ожидаемым результатом;
- **инвариант 8 — обе раскладки атрибутов.** Весь набор тестов политики параметризован двумя
  дублями `SparkSubmitOperator`: приватная раскладка (`_conf`/`_jars` в `template_fields` и в
  `__dict__`, как в 4.1.1) и публичная (`conf`/`jars`, как в 4.10.0). На обеих OL-ключи и jar
  обязаны попасть **в тот же атрибут**, который читает `_get_hook`. Отдельно: раскладка, где имя
  есть в `template_fields`, но атрибута нет; раскладка, где атрибут есть, а в `template_fields` его
  нет; раскладка, где нет ни того ни другого, — во всех трёх политика пишет warning и **не создаёт
  атрибутов** (проверяется сравнением `vars(task)` до и после);
- **инвариант 1**: политика не бросает и не мутирует ни conf, ни jars при
  `HADOOP_CONF_DIR=/nonexistent`, недостижимом NameNode, **незаданной** `OPENLINEAGE_JAR`
  (ветка «пусто → warning», а не `KeyError`), мусоре в `OPENLINEAGE_JAR`,
  `params={"openlineage": "yes"}`, `Param`, бросающем `ParamValidationError`, `task.dag is None`;
- инвариант 1, границы: `AirflowTaskTimeout`, поднятый изнутри политики, **проходит наружу**,
  а не гасится; то же для `AirflowClusterPolicySkipDag` — тест пропускается (`skipif`), если класса
  в установленной версии Airflow нет, и отдельно проверяется, что его отсутствие не ломает импорт
  модуля политики. **Отдельный тест на сборку `_PASSTHROUGH`:** при недоступном
  `AirflowClusterPolicySkipDag` кортеж всё равно содержит `AirflowTaskTimeout` и
  `AirflowClusterPolicyViolation` — то есть отсутствие одного класса не обнуляет весь список
  (регрессия на общий `import` вместо поимённых, §8 инвариант 1);
- инвариант 3: ни conf, ни jars не изменены, если исключение произошло после начала сборки;
  при обрыве между присваиваниями таска остаётся с jar'ом и без листенера, не наоборот;
- **инвариант 4 — три входа по jar'ам.** DAG задал `jars="a.jar"` → в атрибуте jars оба, `a.jar`
  сохранён; DAG задал **только** `conf["spark.jars"]="b.jar"` → `b.jar` попадает в атрибут jars
  вместе с нашим, а сам ключ `conf["spark.jars"]` остаётся равным `"b.jar"` (не переписан и не
  удалён); DAG задал оба → в jars все три без дубликатов и с сохранением порядка. conf, заданный
  в DAG'е, не затирается; повторный прогон политики по той же таске не дублирует jar и не меняет
  результат (идемпотентность, §4.2);
- **инвариант 9**: таска, у которой в conf задан **чужой** `spark.extraListeners`, не трогается
  вовсе — ни conf, ни jars, ни `user_defined_macros`, — и пишется warning; таска, у которой в conf
  лежит **наш** шаблон (результат предыдущего прогона), обрабатывается штатно и warning'а
  «DAG управляет лайниджем сам» **не** получает; объект, который не является экземпляром
  `SparkSubmitOperator`, но чей `operator_class`/`task_type` указывает на него (динамический
  маппинг), даёт warning и не трогается; обычный не-Spark оператор не трогается и warning **не**
  пишет;
- `user_defined_macros` DAG'а не затираются; **при занятом имени `__openlineage_v1` чужим объектом
  политика не инжектит OL и пишет warning**; при этом **две таски одного DAG'а, обработанные
  подряд в одном парсе, обе получают инъекцию** — вторая не считает наш собственный макрос чужим
  (регрессионный тест на проверку по идентичности, а не по наличию ключа; воспроизводит стендовый
  `spark_etl_dag` с двумя `SparkSubmitOperator`);
- **сидинг**: значение, записанное командой из §7, после `Variable.get` + `json.loads` даёт `dict`
  (регрессионный тест на двойное кодирование — вариант с `--json` обязан этот тест валить).

Смоуки:

- `tests/test-policy.bat` → `docker exec hadoop-airflow python -m pytest /opt/airflow/config/tests -q
  -p no:cacheprovider` (каталог смонтирован `:ro`, кэш писать некуда);
- `tests/test-airflow.bat` расширяется: `params={"openlineage": False}` убирает листенер из
  фактически отправленной команды; правка Variable подхватывается следующим запуском без рестарта;
  **DAG с `jars="..."` получает и свой jar, и openlineage-jar в одном `--jars`** — проверка того,
  что канал доставки выбран верно (§4.2); **DAG, задавший свои jar'ы только через
  `conf["spark.jars"]`, доезжает с ними до драйвера** — это единственный способ закрыть открытый
  вопрос §11 про `OptionAssigner`/`mergeFn`, чтением кода он не закрывается.

## 10. Изменения по файлам

| Файл | Изменение |
| --- | --- |
| `airflow/config/airflow_local_settings.py` | сводится к `task_policy` — делегату в `ol_policy.apply_policy` без собственной логики (§4.1) |
| `airflow/config/ol_policy.py` | новый: `apply_policy` (try/except + гейт типа), `inject_openlineage` (сборка и три мутации), `LISTENER`, `MACRO`, `_PASSTHROUGH`, `_warn_once`, `_clean`, `_cfg`, `ol_macro`, `lineage_forced`, `ol_conf_template`, `foreign_listener`, `jar_available`, `merge_jars`, `operator_attrs`, `reset_state` + константы дедлайна/TTL и `_now` |
| `airflow/config/hadoop_conf.py` | новый: `parse_hadoop_xml`, `resolve_webhdfs_urls` (stdlib ElementTree, без mtime-кэша) |
| `airflow/config/tests/` | новый: pytest-набор §9, параметризованный двумя раскладками атрибутов |
| `airflow/config/tests/conftest.py` | новый: кладёт `airflow/config` в `sys.path` + autouse-фикстура `reset_state()` (§9), чтобы набор запускался голым `python -m pytest` без `PYTHONPATH` и не зависел от порядка тестов |
| `airflow/Dockerfile` | `pytest==7.4.0` отдельным слоем под constraints-2.6.3 (в `requirements.txt` не добавляется) |
| `airflow/scripts/start-airflow.sh` | идемпотентный сидинг Variable **без `--json`** + `OPENLINEAGE_CONFIG_RESEED` |
| `docker-compose.yml` | монтаж каталога `./airflow/config` вместо одного файла |
| `env_example` | комментарии к `OPENLINEAGE_*`: что читается в рантайме, что только при первичном сидинге, как перезасеять; `OPENLINEAGE_JAR` — обязательно полный URI со схемой (§5.2) |
| `tests/test-policy.bat` | новый |
| `tests/test-airflow.bat`, `tests/README.md`, `README.md` | тумблер, Variable, `AIRFLOW_VAR_OPENLINEAGE_CONFIG` как аварийный рычаг, неработающий тумблер в форме запуска, и что объявление `Param` с дефолтом `True`/`False` — это форс, а нейтрально только отсутствие ключа либо `Param(None, type=["null", "boolean"], …)` (§5.3) |

Каталог `airflow/config` лежит на `sys.path` как каталог, а не как пакет: `ol_policy` и `hadoop_conf`
импортируются верхнеуровнево, `__init__.py` не заводится. `airflow/config/tests/` при этом остаётся
видимым в `sys.path`; имя `tests` конфликтов на стенде не создаёт, но модули внутри именуются
`test_ol_policy.py` / `test_hadoop_conf.py`, а не обобщённо. В рантайме этот `sys.path` обеспечивает
бутстрап Airflow (`prepare_syspath*`, §2), в тестах — `tests/conftest.py` (§9): двух источников
достаточно, `PYTHONPATH` в командах запуска не появляется.

## 11. Риски и что проверить на этапе плана

- **Раскладка атрибутов между 4.1.1 и 4.10.0 — проверена по обоим тегам, но граница версии, на
  которой произошло переименование, не установлена.** Дизайн от неё и не зависит: `operator_attrs`
  определяет раскладку по факту. План должен проверить это фактическим запуском в обеих средах,
  а не только юнит-тестом на дублях.
- **Spark на стороне кластера в облаке.** Пустой `spark.extraListeners` проверен по исходникам
  **3.5.2**; в стендовом `requirements.txt` стоит `pyspark==3.3.2`, в облачном — `3.5.2`. Сам
  `pyspark` в отправке джобы не участвует (используется бинарь `spark-submit` кластера), но версия
  кластерного Spark в облаке спекой не зафиксирована. Если там 3.3.x — перепроверить тот же ключ по
  тегу 3.3.x, прежде чем полагаться на безопасность пустого значения.
- **`apache-airflow-providers-openlineage` 1.11.0 в облаке.** Опции инжекции parent-job в Spark-conf
  у него нет (проверено), так что конкурирующего писателя в conf таски не появляется. Но если в
  облаке ему задан `transport`, Airflow-level события поедут в тот же Marquez рядом со Spark-level.
  Пересечение namespace и связность графа — вопрос к этапу плана, не к политике.
- **Ревью-цикл по этой спеке не доведён до чистого прохода.** Автоматический gate-цикл останавливался
  на лимите сессии, а не на чистом ревью. Правки третьего и четвёртого проходов ревизии 3 (§12)
  внесены по разбору, но **свежего независимого ревью после четвёртого прохода не было**. Перед
  переходом к плану цикл нужно прогнать заново.
- **Кто разворачивает политику в облаке.** Сидинг Variable (§7) стендовый; в облаке `start-airflow.sh`
  не исполняется, а `airflow db init` там уже устарел в пользу `db migrate`. План обязан назвать, кем
  создаётся `openlineage_config` в облаке и монтируется ли туда каталог `airflow/config` вообще.
- **`SandboxedEnvironment`.** Вызов глобальной функции в песочнице разрешён, но проверяется теперь
  юнит-тестом на настоящем `DAG.get_template_env()` (§9), а не «фактическим запуском» — риск
  переведён в тест. На стенде остаётся подтверждение сквозного прохода до `spark-submit`.
- **Мутация `user_defined_macros` из политики.** Механика подтверждена по `DAG.get_template_env`
  (`env.globals.update`, `cache_size: 0`), но это нестандартный приём — требует явного комментария
  в коде, иначе появление `__openlineage_v1` в DAG'е «из ниоткуда» нечитаемо.
- **Канал доставки jar.** `--jars` побеждает `spark.jars` как источник в
  `SparkSubmitArguments.loadEnvironmentArguments` — это проверено по исходникам Spark 3.5.2 и
  определило выбор атрибута jars оператора вместо ключа `spark.jars` в conf. Не проверено, **как**
  дальше склеиваются значения под YARN-cluster (`OptionAssigner` c `mergeFn`); в докaх Spark 3.5.2
  приоритет и слияние `--jars` против `spark.jars` не описаны вовсе. План обязан закрыть это
  фактическим `spark-submit` на стенде (смоук из §9), а не чтением кода: поведение
  версионно-чувствительно. Дизайн от исхода не зависит: `merge_jars` забирает элементы
  `conf["spark.jars"]` в `--jars` и не трогает сам ключ (§4.2, инвариант 4), поэтому jar'ы DAG'а
  доезжают при любом из двух вариантов слияния — но подтвердить это обязан смоук.
- **`Param(None, type=["null", "boolean"])` как нейтральное объявление тумблера.** Форма взята из
  доков Airflow (раздел Params, «For optional fields that allow empty input, the type must explicitly
  include `null`»), снапшот — 2.11.0; по тегам **2.6.3 и 2.10.2 не перепроверялась**. План обязан
  убедиться, что валидатор `Param` в обеих целевых версиях принимает такое объявление и что
  `dag.params["openlineage"]` отдаёт `None`, а не бросает. Если нет — из §5.3 и `README.md` убирается
  строка про объявленный нейтральный ключ, остаётся «не объявлять ключ вовсе»; на самой политике это
  не сказывается: `lineage_forced` трактует отсутствие ключа и `None` одинаково.
- **Мемо `_cfg` через `lru_cache` живёт процесс воркера.** Для `LocalExecutor`, форкающего процесс
  на таску, это ровно одна таска. Исполнитель облачной среды спекой **не зафиксирован** (§1.1
  перечисляет пины пакетов, не рантайм); на исполнителе с переиспользуемыми процессами мемо станет
  кэшем с неограниченным TTL, и правка Variable перестанет подхватываться «со следующего запуска
  таски», как обещает §5.1. План обязан назвать исполнитель облака и, если процессы там
  переиспользуются, заменить `lru_cache` на кэш с TTL (зонд §6.1 и `_warn_once` §5.4 уже сделаны
  именно так — `_cfg` остался единственным кэшем политики без TTL). Записать это комментарием в коде
  рядом с `lru_cache`. Кэш секретов самого Airflow (`[secrets] use_cache`, 2.10.2) заменой не
  является: он выключен по умолчанию и покрывает только парсинг DAG-ов (§2).
- **`user_defined_macros` и сериализация DAG'ов.** Рендер на воркере идёт от разобранного DAG-файла,
  поэтому макрос там есть. Не проверено, попадают ли `user_defined_macros` (callable) в
  сериализованный DAG и что произойдёт с представлением «Rendered Template» в webserver'е, если
  `rendered_task_instance_fields` для таски пуст и представление попробует отрендерить шаблон от
  сериализованного DAG'а. Отказ, если он есть, ограничен одним представлением UI и не влияет ни на
  парс, ни на исполнение, — но план обязан это проверить и, если фолбэк падает, зафиксировать
  поведение в `README.md`.
- **`task_instance_mutation_hook`: доки расходятся с исходником.** Прозаический раздел доков 2.11.0
  утверждает исполнение на воркере перед запуском таски, а исходник и докстринг `airflow/policies.py`
  — создание TI в шедулере (§2). Дизайн на хук не опирается, но если расхождение разрешится в пользу
  воркера, появится альтернативная площадка инъекции, снимающая весь аппарат §6.1. Проверять — по
  исходникам обеих целевых версий, а не по прозе доков.
- **Не перепроверено для 2.10.2:** лесенка `params` (`DAG.__init__` → `dag.params`, `add_task` не
  подмешивает `dag.params` в `task.params`, `ParamValidationError` из `ParamsDict.__getitem__`) и
  доставка макроса через `DAG.get_template_env` → `env.globals`. От первой зависят четыре строки
  таблицы истинности §5.3, от второй — вся схема с макросом. Оба факта проверены только по тегу
  2.6.3. План обязан перепроверить их по тегу 2.10.2 до реализации.
- **`MappedOperator` до политики.** Спека сознательно не поддерживает динамический маппинг (§4.2) и
  требует warning'а вместо молчаливого пропуска. Не перепроверено, доходит ли `MappedOperator` до
  `task_policy` в обеих версиях. Ветка безопасна при любом исходе, но проверить стоит: если не
  доходит — warning недостижим, и его отсутствие в логе не должно читаться как «маппинг работает».
- **Брошенный демон-поток зонда.** Дедлайн §6.1 ограничивает время **политики**, а не время
  зависшего вызова: поток продолжает висеть в `getaddrinfo`/`connect`. Он ничего не удерживает и
  умирает вместе с процессом, но в долгоживущем процессе при частых парсах таких потоков может
  накопиться до одного на TTL мемо. План обязан убедиться, что мемо действительно исключает второй
  поток по тому же `jar_uri` внутри TTL.
- **Совместимость с существующими DAG'ами.** `spark_pi_dag` и `spark_etl_dag` не задают ни `params`,
  ни `jars=`, ни `conf["spark.jars"]`; после изменения обе таски должны продолжать писать лайнидж при
  полном конфиге в Variable и **не** давать ни одного warning'а на парс: отсутствие ключа
  `openlineage` в `params` — нейтральное молчание (§4.2), а не «уровень не высказался + warning».

## 12. Журнал ревизий

### Ревизия 3 — две целевые среды

Требование: один код политики работает и на `airflow/requirements.txt`, и на
`airflow/requirements_cloud.txt` (§1.1).

| Было (ревизия 2) | Стало | Почему |
| --- | --- | --- |
| `task._conf` и `task._jars` — зашитые имена | `operator_attrs(task)`: имя берётся по совпадению `template_fields` и `hasattr`, незнакомая раскладка — warning и отказ от инъекции (инвариант 8) | в провайдере 4.10.0 атрибуты публичные (`conf`, `jars`); запись по старому имени не падает, а создаёт мёртвый атрибут — лайниджа нет, ошибки нет |
| `defusedxml` — открытый вопрос этапа плана | stdlib `xml.etree.ElementTree`, решение принято | `defusedxml` есть только в облачной среде; зависимость, отсутствующая в одной из целевых, не годится |
| `pytest==7.4.0` как инфраструктура | pytest не входит в `requirements.txt`, ставится слоем образа; набор совместим с 7.4 и 8.3 | в стендовом файле pytest нет вовсе, в облачном стоит 8.3.3 |
| «провайдера `apache-airflow-hdfs` нет в дефолтных extras образа» | ряд убран: провайдер есть в **обеих** средах, отказ от `WebHDFSHook` обоснован его поведением | утверждение было фактически неверным |
| `[scheduler] dag_file_processor_timeout` | `[core] dag_file_processor_timeout` | параметр лежит в секции `[core]` в обеих версиях |
| Тесты бьют по `task_policy` целиком | гейт `isinstance` отделён от сборки; тесты раскладок бьют по функции, принимающей проверенную таску | настоящий оператор в каждой среде даёт только одну из двух раскладок |
| Сидинг Variable подразумевался единственным способом создания | §1.1 п.5: сидинг стендовый, облако создаёт Variable своими средствами или через `AIRFLOW_VAR_*` | `start-airflow.sh` в облаке не исполняется |

Второй проход ревизии 3 — что было исправлено после строгого разбора самой ревизии 3:

| Было | Стало | Почему |
| --- | --- | --- |
| Инвариант 7 обеспечивался таймаутом сокета 2 с и «суммарным бюджетом на процесс» 5 с | перебор эндпоинтов целиком уходит в `threading.Thread(daemon=True)`, вызывающая сторона ждёт `join(5)`; мемо получило TTL (§6.1) | таймаут сокета **не** покрывает `getaddrinfo` (§2), а бюджет-аккумулятор проверялся только перед вызовом и, будучи модульным, в долгоживущем процессе выключил бы зонд навсегда. Зависший DNS обходил обе границы и убивал парс файла |
| Гейт включения — `bool(str(cfg.get("url") or ""))` | валидация значений: `isinstance(str)`, `.strip()`, схема `http://`/`https://` для url (§5.1) | непустота не равна годности: `5000`, `"marquez:5000"`, `" "` проходили `bool()` и роняли конструктор транспорта, а с ним `SparkContext` — тот же класс отказа, что блокер 2 ревизии 2, только вход другой |
| `LISTENER` использовалась в макросе, но нигде не определялась | константа задана явно: `io.openlineage.spark.agent.OpenLineageSparkListener` (§5.4) | значение определяет, поднимется ли лайнидж вообще; §10 при этом убирает единственное место, где оно сейчас лежит |
| Проза обещала warning при неполном конфиге, код-фрагмент `ol_macro` не логировал ничего; кратность не задана | warning в коде + `_warn_once`, один на процесс на ключ; молча выключается только честный `enabled: false` (§5.3, §5.4) | иначе самый вероятный неверный конфиг (`enabled: true` + пустой url из сидинга при незаданном ENV) был бы неотличим от honest-off, а буквальная реализация дала бы три одинаковых warning'а на таску |
| Правило коллизии макроса читалось как «ключ занят → чужое» | условие отказа — `MACRO in macros and macros[MACRO] is not ol_macro` (§4.2) | все таски файла обрабатываются против одного `dag`: наивная проверка выключила бы лайнидж всем таскам, кроме первой, включая обе таски стендового `spark_etl_dag` |

Третий проход ревизии 3 — must-fix, до которых не дошёл автоматический ревью-цикл (он остановился на
лимите сессии, см. §11):

| Было | Стало | Почему |
| --- | --- | --- |
| `_cfg` возвращал `{}` одинаково при отсутствии Variable, битом JSON и не-объекте, а `ol_macro` на отсутствующем `enabled` уходил в `return ""` **до** всякого логирования | каждая из четырёх причин пишет свой `_warn_once`: `no-var`, `bad-json`, `not-object`, `bad-enabled` (§5.4) | §5.3 и §9 обещали «выкл + warning», код-фрагмент молчал. Это блокер 1 ревизии 2 в третьем воплощении: отсутствие Variable — самый вероятный неверный конфиг и он был неотличим от осознанного выключения |
| Строка таблицы «`enabled: false` (либо ключа нет) → выкл, молча» | четыре отдельные строки: молча — **только** при `enabled` ровно `False`; нет Variable / битый JSON / не-объект — warning от `_cfg`; объект без `enabled` или не-`bool` — свой warning; `enabled: true` с негодным url/namespace — warning с именем поля (§5.3) | «либо ключа нет» приравнивало недонастроенный конфиг к осознанно выключенному — ровно та склейка, которую вся ревизия 2 разбирала |
| Тесты требовали различать эти случаи, но не проверяли лог | добавлены: различимость четырёх warning'ов по `caplog`, `enabled: false` → лог **пуст**, объект без `enabled` и `enabled: "yes"` → warning (§9) | три случая отличаются друг от друга **только** логом; тест, не смотрящий в лог, их не отличает |
| Слияние conf не разбирало случай «в DAG'е уже есть `spark.extraListeners`» | инвариант 9: такую таску политика не трогает вовсе и пишет warning (§4.2, §5.3) | иначе свой листенер DAG'а + наш `transport.type` + наш url, отрендеренный в пустоту, роняли `SparkContext` |
| Динамически размапленные таски отсекались `isinstance`, молча | двухступенчатый гейт: Spark-овая по `operator_class`/`task_type`, но не экземпляр → warning «маппинг не поддерживается» (§4.2) | молчаливый пропуск запрещён инвариантом 9; conf/jars у `MappedOperator` лежат в `partial_kwargs`, `operator_attrs` там не работает |
| Инвариант 1 пробрасывал `AirflowTaskTimeout` и `AirflowClusterPolicyViolation` | добавлен `AirflowClusterPolicySkipDag` через защищённый импорт (в 2.6.3 класса нет) | §2 сам фиксирует, что в 2.10.2 он пробрасывается наружу; `except Exception` проглотил бы чужое решение пропустить DAG |
| §2 утверждала, что webserver парсит DAG-файлы | утверждение снято: webserver читает сериализованные DAG'и из БД | иначе спека несла два взаимоисключающих варианта — «процесс живёт один парс» и «в веб-процессе модульное состояние живёт вечно» |
| Зонд по эндпоинтам: «404 → False, иной код → False с warning» | `403` + `StandbyException` → следующий эндпоинт (§6) | первый же standby в HA-списке выключал бы лайнидж на исправном кластере — против того, ради чего оставлен полный резолвер |
| `operator_attrs` читала `template_fields` с типа | читает с экземпляра | Airflow при рендере обращается к `self.template_fields`; оператор, переопределивший список на экземпляре, проверялся бы по одному списку, а рендерился по другому |
| `os.environ["OPENLINEAGE_JAR"]` в псевдокоде §4.2 при описанной ветке «пусто» | `os.environ.get(..., "")`; в псевдокод добавлены `dag = task.dag` и ветка `dag is None` | индексация давала `KeyError` вместо warning'а, а имя `dag` в схеме было ничем не связано |
| Инварианты 5, 6 и 9 не имели покрывающих пунктов в §9 | добавлены: ключ `auth`, отсутствие значений в логах, «ноль `Variable.get` на парсе», чужой листенер, маппинг, две таски в одном парсе | §9 обязан покрывать каждый инвариант §8 |
| §9 не задавала, как тесты находят модули | `airflow/config/tests/conftest.py` кладёт `airflow/config` в `sys.path` (§9, §10) | команду запуска нельзя было написать без доугадывания `PYTHONPATH` |
| Факты о `params` и `get_template_env` подавались как общие | явно помечены как проверенные только по 2.6.3 и вынесены в §11 | ревизия 3 их по тегу 2.10.2 не перепроверяла, а от них зависит половина таблицы истинности §5.3 |

Четвёртый проход ревизии 3 — по разбору третьего прохода:

| Было | Стало | Почему |
| --- | --- | --- |
| `_cfg` возвращал `{}` и при отказе, и при успешном разборе значения `{}`; `ol_macro` подавлял повторный warning условием `if cfg` | `_cfg` возвращает `None` при отказе и dict при успехе; `ol_macro` различает их по типу (§5.4), в §5.3 и §9 добавлен явный случай `{}` | валидная Variable со значением `{}` (типовая правка в UI) не давала warning'а ни в `_cfg` (там путь успешный), ни в `ol_macro` (там `{}` — falsy): «лайниджа нет, в логе пусто» — блокер 1 ревизии 2 в четвёртом воплощении |
| Инвариант 9 требовал warning при чужом `spark.extraListeners` «безусловно», а последняя строка §5.3 — при «любом» значении `params`, тогда как §4.2 проверяет `forced is False` раньше | порядок гейтов §4.2 объявлен нормативным; строка §5.3 сужена до «не `False`»; инвариант 9 сам называет форс-выключение более сильным случаем | для комбинации «форс-выключение + свой листенер в conf» спека требовала одновременно warning (таблица) и тишину (инвариант 9) — реализовать оба нельзя, реализатор угадывал |
| Гейт по `spark.extraListeners` проверял **наличие** ключа | `foreign_listener`: чужим считается значение, не совпадающее с шаблоном политики (§4.2) — симметрично проверке макроса по идентичности | повторный прогон по той же таске видел собственный шаблон и писал вводящий в заблуждение warning «DAG управляет лайниджем сам»; §9 при этом требует теста на повторный прогон, то есть спека противоречила сама себе |
| §9 требовала вынести сборку в отдельную функцию, но ни §4.1, ни §4.2, ни §10 её не называли | таблица границ в §4.1: `task_policy` (делегат) → `apply_policy` (try/except + гейт типа) → `inject_openlineage` (всё остальное), обе в `ol_policy.py` | имя, модуль и граница были предметом доугадывания ровно там, где решается тестируемость обеих раскладок |
| Сброс модульного состояния между тестами не задан | `ol_policy.reset_state()` + autouse-фикстура в `tests/conftest.py`; пороги и `_now` — модульные, подменяются `monkeypatch.setattr` (§9, §6.1) | `_warned`, `lru_cache` у `_cfg` и мемо зонда переживают границу теста, а `caplog` — нет: тесты, объявленные защитой от блокера 1, зеленели бы по порядку запуска |
| Рекомендация объявлять тумблер как `Param(False, …)` «чтобы показать описание» | три варианта по намерению; нейтрально — не объявлять ключ либо `Param(None, type=["null", "boolean"], …)` (§5.3, риск в §11) | `Param` резолвится в дефолт: сниппет ради подписи молча выключал лайнидж всему DAG'у — тот же класс отказа, ради которого сделана вся ревизия |
| «Не высказался» и «не высказался + warning» не были разведены | отсутствие ключа и значение `None` — молча; warning только при присутствующем не-`bool` (§4.2, §5.3, §9) | по букве прежней формулировки обычный DAG без тумблера писал бы warning на каждую таску каждого парса — раз в 30 секунд на файл бессрочно |
| `merge_jars(current, jar)` — два источника | `merge_jars(current, conf_jars, jar)`: элементы `conf["spark.jars"]` DAG'а забираются в `--jars`, сам ключ не трогается (§4.2, инвариант 4, тест и смоук §9) | смена канала доставки отбирала jar'ы у DAG'ов, задающих их через `conf["spark.jars"]`: явный `--jars` вытесняет `spark.jars` как источник (§2) |
| `AirflowClusterPolicySkipDag` импортировался «через `try/except ImportError`» без указания, общий это импорт или поимённый | кортеж `_PASSTHROUGH` собирается поимённо, каждый класс своим `try/except`; общий `import` явно запрещён (§8, инвариант 1) + тест §9 | общий `from airflow.exceptions import A, B, C` на 2.6.3 провалился бы целиком из-за третьего имени и молча выключил бы проброс `AirflowTaskTimeout` — ровно на той среде, где гонка §6.1 вероятнее всего |
| Форма значения `OPENLINEAGE_JAR` не была задана | §5.2: схема обязательна, путь — `urlparse().path` в `/webhdfs/v1<path>`, authority игнорируется, ключ мемо — исходная строка | реализатор угадывал минимум три развилки; ошибка в любой давала либо ложное «jar есть» (опрошен не тот кластер), либо ложное «jar нет» (молча выключённый лайнидж) |
| Исход «standby с последнего эндпоинта» не определён | `False` с warning, отличимым по тексту от «эндпоинты недоступны» (§6, тест §9) | сценарий «все NameNode в standby» — ровно тот, ради которого ветка standby и заведена, и он был единственным без заданного исхода |
| `_warned` — модульное множество без TTL, риск в §11 не назван | дедупликация с `_WARN_TTL_SEC` 300 с через тот же `_now`, что и мемо зонда (§5.4) | на исполнителе с переиспользуемыми процессами первый же warning гасил ключ навсегда, и все последующие таски выключали лайнидж молча — диагностическая половина того же блокера |
| Инвариант 8 утверждал, что приватные/публичные имена встречаются «только в §2» | формулировка сужена до проверяемой: ни одно чтение и ни одно присваивание не адресует атрибут по фиксированному имени | утверждение было ложно в тексте, который его же декларирует (§4.2 и §12 называют имена прозой), и как критерий следующего ревью не работало |
| §3 обещала «≤1 HTTP GET на парс файла» | «≤1 вызов зонда»; число HTTP-запросов внутри вызова ограничено числом эндпоинтов и дедлайном (§3, §6.1) | мемо сводит к одному число вызовов, а не запросов: на HA-контуре, ради которого оставлен резолвер, расхождение наблюдаемо |

Что ревизия 3 **подтвердила** без изменений: расположение `task_policy` вне таймаута импорта,
семантика `--json` у `variables set`, `SystemExit` у `variables get`, значения таймаутов и интервала
парсинга, `$AIRFLOW_HOME/config` в `sys.path`, порядок `import_local_settings` до `configure_orm` —
всё это в 2.10.2 такое же, как в 2.6.3. Кэш секретов в 2.10.2 появился, но по умолчанию выключен и
покрывает только парсинг DAG-ов, поэтому мемоизацию в макросе не заменяет.

### Ревизия 2 — после строгого разбора

Изменения относительно ревизии 1 — что именно было неверно и чем заменено.

| Было (ревизия 1) | Стало | Почему |
| --- | --- | --- |
| `airflow variables set --json` в §7 | без `--json`, сборка JSON питоном, `OPENLINEAGE_CONFIG_RESEED` | `--json` = «сериализовать», а не «уже JSON»: двойное кодирование давало молча выключенный лайнидж, замаскированный собственной сетью безопасности |
| При форсе — литеральный листенер, `url` отдельным шаблоном | всё через один макрос; гейт = (форс или `enabled`) и непустые `url`, `namespace` | форс без Variable включал листенер с пустым url → падение `SparkContext`, то есть возврат того самого отказа, против которого написан зонд |
| Инвариант 1 «политика не влияет на успешность парса», обеспеченный `try/except` | инвариант 1 (исключения) + инвариант 7 (время: таймаут 2 с, бюджет 5 с, мемо) | `task_policy` работает вне `timeout(dagbag_import_timeout)`; зависший сокет — отказ по времени, `try/except` его не покрывает, а `dag_file_processor_timeout` убивает парс файла целиком |
| Зонд на каждую таску, «1 HTTP GET» в §3 | мемо по `jar_uri` + общий бюджет | внутреннее противоречие §3 против §4.2 |
| jar через `spark.jars` в conf | jar через атрибут jars оператора (в ревизии 2 — зашитый `task._jars`, в ревизии 3 имя резолвится) | `--jars` из DAG'а вытеснял бы `spark.jars` как источник → листенер без jar'а → `ClassNotFoundException` |
| «1 обращение к БД на запуск таски» | 1 благодаря мемо; без мемо было бы 3 | конфиг читается в трёх значениях conf таски |
| Макрос `ol_cfg`, «не затирая чужие» | версионированное имя `__openlineage_v1`, при коллизии — не инжектить | неуникальное имя + «не затирать» = наши шаблоны зовут чужую функцию |
| mtime-кэш разбора XML «даёт подхват без рестарта» | кэш снят | конфиги запечены в образ, процесс живёт один парс — подхватывать и переживать нечего |
| §1 цель 3 «проверять по конфигам кластера» без оговорок | та же цель с явным мотивом «снятие допущения об одном NN на 9870» | на стенде резолв гарантированно уходит в фолбэк и даёт тот же `namenode:9870` — выгода отложенная, а не текущая |
| Про `params` не сказано, что тумблер виден в форме запуска | `Param(..., description=...)` + абзац в README | иначе пользователю показан редактируемый переключатель, который ничего не делает |
| `SandboxedEnvironment` — риск «проверить на стенде» | юнит-тест на `DAG.get_template_env()` | это чистый unit, стенд не нужен |
| Про `AIRFLOW_VAR_OPENLINEAGE_CONFIG` и разовость ENV-сидинга не сказано | §5.2 | «поправил `.env` / правлю в UI — не подхватывается» — два разных способа потерять час |

## 13. Решения, принятые на реализации

Спека была недоопределена в семи точках; реализация закрыла их перечисленными ниже решениями.
Они **нормативны наравне с остальными разделами** и там, где расходятся с буквой предыдущих
разделов, побеждают. Разделы 3, 8 и 9 приведены в соответствие правкой по месту.

### D1. Контракт зонда

`jar_available(jar_uri: str, path: str) -> bool`. Ключ мемо — `jar_uri` (исходное значение
`OPENLINEAGE_JAR`); проверка мемо и запись в мемо — на **ожидающей** стороне, до и после ожидания.
В демон-поток уходит **весь** перебор, включая резолв эндпоинтов. Поток кладёт в слот ровно одно
из двух: `("ok", bool)` либо `("err", exc)`. Ожидающая сторона после `join(_PROBE_DEADLINE_SEC)`
разбирает три исхода:

| Исход | Результат | Сообщение | Ключ `_warn_once` |
| --- | --- | --- | --- |
| `("ok", value)` | `value` | — | — |
| `("err", exc)`, `exc` — `_NoEndpointsError` | `False` | «OpenLineage не включён: эндпоинты WebHDFS не определены по HADOOP_CONF_DIR (%s)» | `("no-endpoints",)` |
| `("err", exc)`, прочее | `False` | «OpenLineage не включён: не удалось определить эндпоинты WebHDFS (%s): %s» | `("probe-error",)` |
| слот пуст (дедлайн истёк, поток жив) | `False` | «OpenLineage не включён: зонд jar не уложился в дедлайн %s с (%s)» | `("probe-deadline",)` |

Пустой список эндпоинтов от резолвера — отдельный исход. Чтобы поток сохранял контракт «ровно одно
из двух», он поднимает `_NoEndpointsError(hadoop_conf_dir())`, а ожидающая сторона отличает его по
типу. Поэтому `resolve_webhdfs_urls` возвращает **пустой список**, а не поднимает `RuntimeError`,
как оригинал в `SparkAPI`; ветка «`err`» при этом остаётся и покрывает битый либо отсутствующий XML.

Пять текстов зонда (три выше плюс «все NameNode ответили standby» и «эндпоинты WebHDFS недоступны»
из §6) попарно различны, у каждого свой ключ дедупликации; различность закреплена тестом. Результат
кладётся в мемо во **всех** исходах, включая отказные.

### D2. Валидация формата `OPENLINEAGE_JAR` — вне зонда

`jar_path(jar_uri: str) -> str | None` — чистая функция без логирования, правила §5.2.
`inject_openlineage` зовёт её **перед** `jar_available`; `None` → warning и `return`, зонд не
вызывается вовсе. Причина отказа различается вызывающей стороной: пустое значение даёт
«`OPENLINEAGE_JAR` не задан» (ключ `("jar-unset",)`), непустое негодное — «задан без схемы или без
пути» (ключ `("jar-malformed",)`). `jar_available` формат не проверяет и получает уже разобранный
путь вторым аргументом.

### D3. Предупреждение о негодном тумблере — внутри `lineage_forced`

`lineage_forced(task) -> bool | None` возвращает **только** `True`, `False` либо `None`. Значение,
присутствующее в `params`, но не `bool` и не `None`, пишет warning и трактуется как отсутствующее:
обработка идёт дальше по лесенке `task.params` → `dag.params` → `None`. Наружу негодное значение не
отдаётся никогда. Исключение при чтении `params` обрабатывается так же и имеет свой текст и ключ.

### D4. Ключи дедупликации заданы для всех предупреждений политики

| Класс причины | Ключ | Причины |
| --- | --- | --- |
| зависящие от таски | `(причина, dag_id, task_id)` | чужой `spark.extraListeners`, динамический маппинг, незнакомая раскладка атрибутов, `task.dag is None`, занятое имя макроса, негодный или нечитаемый тумблер, недоступный jar, непредвиденная ошибка политики |
| общие на процесс | `(причина,)` | пустая либо бессхемная `OPENLINEAGE_JAR`, все причины отказа `_cfg` и `ol_macro`, все пять исходов зонда |

Так инвариант 9 держится: каждая пропущенная таска попадает в лог собственной строкой, а процессные
причины не спамят. Отсюда две правки к §5.4: ключ `_warn_once` — кортеж (`tuple[str, ...]`), а не
строка, и у функции появился параметр `exc_info: bool = False` — иначе общий обработчик
`apply_policy`, которому инвариант 1 предписывает `exc_info=True`, оказался бы единственным
предупреждением без ключа дедупликации.

### D5. `_PASSTHROUGH` собирается лениво

Кортеж собирается при **первом вызове** `apply_policy` и кэшируется в модульной переменной — не на
импорте модуля. §8 требовал обратного, но §9 и §4.1 сильнее: `airflow_local_settings` импортируется
из `settings.initialize()` **до** `configure_orm()`, и импорт подмодуля Airflow из частично
инициализированного пакета на импорте политики — риск, которого сборка по первому вызову не имеет.
Сборка идёт поимённо через `importlib.import_module("airflow.exceptions")` + `getattr`;
отсутствующее имя пропускается, отсутствующий модуль даёт пустой кортеж. §8 инвариант 1 исправлен
по месту. Кэш сбрасывается в `reset_state()` вместе с остальным модульным состоянием — иначе тест
сборки кортежа зависел бы от порядка запуска.

### D6. `merge_jars` не режет значение с Jinja

Если в строке есть `{{` или `{%`, она считается одним неделимым элементом и по запятой не
разбивается: атрибут jars и `conf["spark.jars"]` шаблонизируются Airflow, и разбиение на парсе
резало бы выражение с запятой внутри на два невалидных куска — нарушение инвариантов 2 и 4
одновременно. Покрыто тестом на трёх формах: `{{ ... | join(', ') }}`, вызов с двумя аргументами,
блок `{% if %}`.

### D7. Тест дедлайна меряет реальное время

`_PROBE_DEADLINE_SEC` уменьшается `monkeypatch`'ем до долей секунды, а прошедшее время меряется
`time.monotonic()`. Подменённый `_now` остаётся источником времени только для мемо и TTL
`_warn_once`, где его действительно читает код политики; ожидание внутри
`Thread.join(_PROBE_DEADLINE_SEC)` идёт по реальным часам и подменённый `_now` его не видит.
Формулировка в §9 исправлена по месту.

### D8. Строка 4 таблицы §3 переименована

«Форс/`enabled`, `url`, `namespace`» → «Применение `enabled` и подстановка `url`, `namespace`»: форс
читается и решается целиком на парсе (строка 2 той же таблицы), на рендере он только комбинируется
с `enabled` внутри `ol_macro`. Прежняя формулировка давала на один вопрос два взаимоисключающих
ответа. Исправлено по месту.

### Прочие уточнения реализации

| Что | Как сделано | Почему |
| --- | --- | --- |
| Распознавание оператора | `_spark_submit_operator()` — отдельная функция, возвращающая класс провайдера либо `None` | без неё набор §9 не мог бы проверять гейт типа в среде без провайдера; `None` (провайдера нет) — тихий `return`, политике нечего делать |
| Сообщения политики | модульные константы `_MSG_*` | §9 требует проверять различимость текстов; сравнивать литералы из тела функций тест не может |
| `merge_jars(current, conf_jars, jar)` | первые два параметра типизированы как `object` | §4.2 требует «`None` и не-строка дают пустой вклад», а `str \| None` этого не выражает |
| `OPENLINEAGE_CONFIG_RESEED` | добавлен в `environment` сервиса `airflow` в `docker-compose.yml` | окружение сервиса перечислено явно; без этой строки заявленный §5.2 рычаг из `.env` до контейнера не доезжает |
| Смоук `tests/test-airflow.bat` | проверяет `--jars` и `spark.extraListeners` в команде, собранной `SparkSubmitHook._build_spark_submit_command`, а не в отправленной джобе | новых DAG-файлов §10 не предусматривает, а команду строит тот же код провайдера, что и при запуске таски; доезд jar'а до драйвера подтверждает существующая проверка лайниджа в Marquez |
