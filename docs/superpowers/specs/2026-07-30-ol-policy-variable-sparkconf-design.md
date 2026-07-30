# Cycle 1: Variable хранит `spark_conf` целиком и `openlineage_jar`, без чтения Variable на парсе

**Дата:** 2026-07-30
**Статус:** утверждён (brainstorming), готов к плану реализации
**Артефакт:** `airflow/config/ol_policy/__init__.py`, `airflow/scripts/start-airflow.sh`, `env_example`, `docker-compose.yml`, `airflow/config/tests/test_ol_policy.py`, `airflow/config/tests/conftest.py`, `README.md`, `tests/README.md`, `airflow/Dockerfile`
**Предшественник:** [2026-07-29 OpenLineage policy config](2026-07-29-openlineage-policy-config-design.md), §10 которого уже предусматривает этот цикл «в два захода»

## 1. Контекст и цель

Текущий контракт Variable `openlineage_config` — `{enabled, url, namespace}`: политика
знает список из трёх инжектируемых ключей и вставляет их в conf таски литералами или через макрос.
Сидинг `airflow/scripts/start-airflow.sh:34-44` уже пишет новый формат
(`{enabled, spark_conf, openlineage_jar}`), но код `ol_policy/__init__.py` его **не понимает**:
`_cfg()` ждёт поля `url`/`namespace`, `inject_openlineage` читает jar URI из env `OPENLINEAGE_JAR`,
зонд jar работает на парсе через `jar_available`. Сейчас стенд после рестарта попадает в ветку
«нет Variable / битый JSON → лайнидж выключен» — это рассогласование и есть задача.

Цели:

1. **Привести код в соответствие с уже сидимым форматом Variable**, чтобы правка в UI
   (Admin → Variables) работала со следующего запуска таски, без рестарта контейнера и без правки `.env`.
2. **Снять чтение Variable с парса** — расписание (`_bag_dag` в DAG-file-processor'е шедулера) и
   парс на воркере перед запуском таски не должны трогать метастор. Текущий `_cfg` уже вызывается
   только из `ol_macro` на рендере; требование — зафиксировать это как инвариант и не сломать его
   при переносе jar'а в Variable.
3. **Источник правды для `spark.extraListeners` — Variable.** Раньше класс листенера жил хардкодом
   в `LISTENER` и подставлялся всегда при включённом lineage. В новой схеме политика берёт класс
   из `spark_conf["spark.extraListeners"]` либо доверяет DAG'у, который задал ключ в своём conf;
   ни одного зашитого класса в политике быть не должно.

**Жёсткие правила этого цикла** (получены уточнения пользователя при ревизии):

- **OL побеждает по URL/namespace.** Если `spark.openlineage.transport.url` /
  `spark.openlineage.namespace` задан и в Variable, и в DAG-conf — итог в conf берётся из Variable,
  в лог пишется info с обоими значениями.
- **`spark.extraListeners` мерджится с DAG-listener'ами.** Если DAG передал свой CSV
  (`"com.example.A,com.example.B"`) в `conf["spark.extraListeners"]`, а OL-listener задан в
  Variable — итог `com.example.A,com.example.B,<OL-listener>` через `merge_listeners`
  (дедуп, сохранение порядка: DAG-listener'ы первыми, OL-listener последним).
- **Jar мерджится, не затирает.** Если DAG задал `jars="a.jar"` или `conf["spark.jars"]="a.jar"`,
  политика дописывает свой openlineage-jar через `merge_jars` (как в предке).
- **Никаких silent-веток.** Каждое решение политики (включили / выключили / конфиг негоден /
  jar не подмешан / DAG-conf перебит) пишет info- или warning-лог. Дедуплицируется только
  единственный warning о неполной Variable через `_validate_cfg` (один на процесс).

**Не входит в скоуп:** поддержка `apache-airflow-providers-openlineage` 1.11.0 как конкурирующего
писателя в conf (риск из §11 предка — проверяется на стенде, политика на этот пакет не опирается);
новые переменные окружения; проброс произвольных `spark_conf.*` ключей из Variable в conf
(например, `spark.openlineage.columnLineage.datasetLineageEnabled`) — отдельная итерация; кэш
секретов Airflow 2.10.2 ([secrets] use_cache по умолчанию False — не покрывает рендер, §2 предка);
перенос `apache-airflow-providers-apache-spark` 4.1.1 → 4.10.0 (инвариант 8 предка уже опирается
на `operator_attrs`); цикл 2 из §10 предка (вынос переменной `enabled` в DAG-level overrides,
отдельный дизайн).

## 2. Грундинг-бриф — обязателен во всех брифах реализации

Все факты ниже перепроверены по исходникам тегов, приведённых в §2 предка
(`apache-airflow/2.6.3`, `apache-airflow/2.10.2`, `apache-airflow-providers-apache-spark/4.1.1`,
`apache-airflow-providers-apache-spark/4.10.0`, `spark/v3.5.2`); что меняется в этом цикле —
помечено явно.

| Факт | Источник | Что меняется |
| --- | --- | --- |
| `task_policy` исполняется на парсе (scheduler и воркер при запуске таски); webserver парсит только сериализованные DAG'и | `airflow/models/dagbag.py:480` (тег `2.6.3` и `2.10.2`) | ничего |
| `_bag_dag` и `task_policy` исполняются **вне** `timeout(dagbag_import_timeout)` — зависший сетевой вызов не даёт исключения, а съедает `[core] dag_file_processor_timeout` (дефолт 50 с) | `airflow/models/dagbag.py:475-490` (оба тега) | ничего |
| Webserver читает сериализованные DAG'и через `read_dags_from_db=True`, файлы не парсит | `airflow/models/dagbag.py::collect_dags_from_db`; [DAG Serialization 2.11.0](https://airflow.apache.org/docs/apache-airflow/2.11.0/administration-and-deployment/dag-serialization.html) | ничего |
| `Variable.get` с `deserialize_json=True` делает `json.loads` без `try/except`; `default_var` спасает только от отсутствия переменной | `airflow/models/variable.py::get` (оба тега) | ничего — `_cfg` уже не бросает |
| `EnvironmentVariablesBackend` стоит **первым** в `DEFAULT_SECRETS_SEARCH_PATH`; `AIRFLOW_VAR_OPENLINEAGE_CONFIG` перекрывает метастор | `airflow/secrets/__init__.py:33`; `airflow/secrets/environment_variables.py:49` | ничего |
| `apache-airflow-providers-openlineage` 1.11.0 в облачной среде не имеет опций инъекции parent-job в Spark-conf; конкурирующего писателя в conf таски не появляется | `airflow/providers/openlineage/provider.yaml` (тег `1.11.0`) | ничего |
| `_get_hook` провайдера читает `self._conf` (4.1.1) либо `self.conf` (4.10.0); шаблонизация conf входит в `template_fields` — Jinja рендерится на воркере перед запуском таски | `spark_submit.py:75-79, 126, 166` (тег `4.1.1`); то же в `4.10.0` для публичных имён | ничего — `operator_attrs` адресует оба варианта |
| `--jars` оператора в `SparkSubmitArguments.loadEnvironmentArguments` стоит **раньше** `spark.jars`: `Option(jars).orElse(sparkProperties.get(JARS.key))` — явный `--jars` вытесняет `spark.jars` как источник | `core/.../deploy/SparkSubmitArguments.scala` (тег `v3.5.2`) | ничего — мердж через `merge_jars` в этой итерации кладёт итог в `conf["spark.jars"]`, атрибут `jars` не пишется |
| Пустой `spark.extraListeners` безопасен: `.stringConf.toSequence.createOptional`, `Utils.stringToSeq` фильтрует пустые, `loadExtensions` делает `flatMap` | `core/.../internal/config/package.scala:1423-1428`, `core/.../util/Utils.scala:2754-2772` (тег `v3.5.2`) | ничего |
| Полное имя класса листенера — `io.openlineage.spark.agent.OpenLineageSparkListener`; сопутствующие ключи `spark.openlineage.transport.type`/`.url`/`spark.openlineage.namespace` | доки OpenLineage | имя **больше не хардкодится** в политике — Variable owns listener, §4 |
| Зонд HDFS через демон-поток, мемо по URI с TTL 300 с, дедлайн 5 с — перенесён из §6.1 предка **без изменений**: единственный stdlib-примитив с гарантированным дедлайном, покрывает `getaddrinfo` (которому таймаут сокета не передан) | предок §6.1 | **место вызова**: с парса (`inject_openlineage`) → на рендер (`ol_macro` → `_resolve_jar`), §4 |
| `airflow variables set --json` означает сериализовать, а не «значение уже JSON»: `Variable.set(key, value, serialize_json=args.json)` при `serialize_json=True` делает `json.dumps(value, indent=2)` | `airflow/cli/commands/variable_command.py::variables_set`; `airflow/models/variable.py::set` | ничего — `start-airflow.sh` уже пишет без `--json` |
| `airflow variables get` при отсутствии ключа поднимает `SystemExit` — ненулевой код возврата, пригоден для идемпотентного сидинга | `airflow/cli/commands/variable_command.py:41-51` | ничего |
| `TaskInstance.render_template` рекурсивно рендерит значения `dict` в `template_fields`; `conf` оператора входит в `template_fields` | `airflow/models/taskinstance.py:1531`; `spark_submit.py:75-79` | новое: на рендере политика вызывает `_cfg` (один раз на процесс), `_validate_cfg` (один раз на процесс), `_resolve_jar` (один раз на URI); `_render_jar_merge` кладёт `merge_jars(...)` в `conf["spark.jars"]` |
| `DAG.get_template_env()` строит `SandboxedEnvironment` с `cache_size: 0` — каждый рендер заново | `airflow/models/dag.py::get_template_env` | ничего |
| `ParamsDict.__getitem__` бросает `ParamValidationError`; `MutableMapping.get` её не ловит | `airflow/models/param.py` | ничего — `_level_forced` уже под `try/except` |
| Jinja `template_local` позволяет достать значение **текущего** атрибута оператора / ключа conf до того, как результат макроса записан обратно | [Jinja2 docs — Context locals](https://jinja.palletsprojects.com/en/3.1.x/api/#the-context) | новое: `_resolve_local("jars")` и `_resolve_local("spark.jars")` читают текущие значения без Variable/HDFS |

**Чего эта ревизия не меняет и не перепроверяет:** иерархия гейтов §4.2 предка, инварианты 1–9
предка (кроме инварианта 6, чьё тело сдвигается на рендер), две раскладки провайдера, ленивая
сборка `_PASSTHROUGH`, `merge_jars` с Jinja-неделимостью, `foreign_listener` по значению.

## 3. Ключевое следствие: где что решается

Решение предка — «`enabled`/`url`/`namespace` читаются на рендере через макрос, jar URI живёт в env
и читается на парсе для зонда». В этом цикле распределение меняется:

| Решение | Момент | Где исполняется | Обращения к БД | Сеть |
| --- | --- | --- | --- | --- |
| Это подходящая таска (тип и раскладка атрибутов)? | парс | форк DAG-file-processor'а, процесс запуска таски на воркере | 0 | нет |
| Форс вкл/выкл из DAG'а (`params`) | парс | там же | 0 | нет |
| Применение `enabled` и подстановка `spark_conf` в conf | **рендер** | только воркер | **1 на процесс** (`_cfg` мемоизирован, `_validate_cfg` вызывается на каждый `ol_macro`) | нет |
| Чтение `openlineage_jar` из Variable и HDFS-зонд | **рендер** | только воркер | 0 (jar URI уже в `_cfg`) | ≤1 вызов зонда на URI (мемо по URI) |

Сравнение с предком: **источник jar URI сдвинулся с env на Variable**, **зонд сдвинулся с парса на
рендер**. Всё остальное — без изменений.

Три следствия, которые делают этот сдвиг обязательным:

1. **Probe на парсе под запретом.** Парс работает в scheduler/dag bag и в воркере перед запуском
   таски — Variable там не читается (жёсткое правило). Если бы probe остался на парсе, источник
   jar URI пришлось бы оставить в env — расходится с целями 1 и 3.
2. **Probe на рендере безопасен по времени.** Зонд под дедлайном 5 с в демон-потоке, мемо с TTL
   300 с — весь аппарат §6.1 предка переносится без правок. Единственное отличие — лишних 5 с
   бюджет расходуется на воркере, а не на парсе файла. Для DAG-file-processor'а это строго
   выигрыш: `[core] dag_file_processor_timeout` (50 с) больше не делит время с зондом.
3. **Jar мерджится с тем, что передал DAG.** DAG, задавший `jars="a.jar"` или
   `conf["spark.jars"]="a.jar"`, получает **`a.jar + openlineage-jar`**, а не просто
   перезаписывается нашим. Два канала (`jars` и `conf["spark.jars"]`) объединяются через
   `merge_jars(operator.jars, conf["spark.jars"], our_jar)` — те же правила, что в предке.
   Дополнительно: OL-`spark_conf` по **lineage-ключам** мерджится **поверх** DAG-conf
   (`OL побеждает по ключам lineage` — жёсткое правило); не-lineage `spark.*` остаются за
   пользователем.

## 4. Архитектура

### 4.1 Изменения в `inject_openlineage` (парс)

```python
def inject_openlineage(task: object) -> None:
    # ... гейты 1-4 без изменений: operator_attrs, lineage_forced, dag, MACRO ...
    macros = dict(getattr(dag, "user_defined_macros", {}) or {})
    macros[MACRO] = ol_macro
    dag.user_defined_macros = macros            # только макрос; conf и jars — на рендере
```

**`inject_openlineage` больше не пишет ни в `conf`, ни в `jars`.** Только регистрирует макрос
в `dag.user_defined_macros`. Все мутации conf и jars — на рендере в `ol_macro`, где `_cfg` уже
прочитан и `jar_available` уже вызван (или закэширован мемо).

Это **откат к предковой схеме «всё через макрос»**, но с тремя отличиями:

- Маппинг `field → как мерджить` живёт в `ol_macro`. `field="jar"` → добавить jar в `spark.jars`
  с дедупликацией против того, что задал DAG (через `_render_jar_merge`). `field="listener"`/`url`/
  `namespace` → записать значение, **OL побеждает по ключам lineage** (жёсткое правило).
- `ol_macro('jar')` сам ходит в `jar_available` и возвращает результат `merge_jars` через
  `_render_jar_merge`, который читает **текущие** значения `jars` и `conf["spark.jars"]` через
  Jinja-локалы (`_resolve_local`) и дописывает свой URI.
- Парс больше **не знает** про Variable и **не зондирует** HDFS. Инвариант 6 усиливается до
  «на парсе ноль обращений к Variable, метастору и HDFS».

### 4.2 Контракт `ol_macro` и `_validate_cfg` (рендер)

```python
@functools.lru_cache(maxsize=1)
def _cfg() -> dict[str, object] | None:
    """Конфиг OL из Airflow Variable. Никогда не бросает: при любой ошибке — None.

    Никогда не вызывается на парсе — инвариант 6. Только из ``_validate_cfg`` и ``ol_macro``
    на воркере в момент рендера.
    """
    # ... без изменений: Variable.get, json.loads, isinstance(parsed, dict), auth-warning ...
    return parsed


def _validate_cfg(cfg: dict[str, object] | None) -> dict[str, object] | None:
    """Валидирует Variable **один раз на процесс**: список недостающих полей — одним warning'ом.

    ``_cfg`` уже мемоизирован, и для одного процесса воркера это один вызов на всё. Если
    Variable негоден, возвращает ``None``; ``ol_macro`` в этом случае пишет отдельный
    warning «конфиг не валиден» и возвращает ``""``.

    Это и есть «**один раз проверить при триггере**»: одна валидация — один warning.

    :param cfg: разобранный dict из ``_cfg`` либо None.
    :return: тот же cfg, либо None, если он непригоден.
    """
    if cfg is None:
        logger.warning("ol_policy: Variable openlineage_config не задана или не JSON-объект — lineage не включён")
        return None
    missing: list[str] = []
    if not isinstance(cfg.get("enabled"), bool):
        missing.append("enabled (bool)")
    spark_conf = cfg.get("spark_conf")
    if not isinstance(spark_conf, dict):
        missing.append("spark_conf (object)")
    else:
        if not _clean(spark_conf.get("spark.extraListeners")):
            missing.append("spark_conf.spark.extraListeners (non-empty string)")
        url = _clean(spark_conf.get("spark.openlineage.transport.url"), require_scheme=True)
        if not url:
            missing.append("spark_conf.spark.openlineage.transport.url (http/https URL)")
        if not _clean(spark_conf.get("spark.openlineage.namespace")):
            missing.append("spark_conf.spark.openlineage.namespace (non-empty string)")
    jar_uri = cfg.get("openlineage_jar")
    if not (isinstance(jar_uri, str) and jar_uri.strip()):
        missing.append("openlineage_jar (hdfs://... URI)")
    if missing:
        logger.warning("ol_policy: Variable openlineage_config неполна, lineage не включён: %s", ", ".join(missing))
        return None
    return cfg


def ol_macro(field: str, forced: bool | None = None, dag_cur: str | None = None) -> str:
    """Рендер-функция. Вызывается Jinja на воркере.

    Семантика по ``field``:

    - ``"listener"`` → значение ``spark.extraListeners`` из Variable после ``_clean``.
      Если OL включён и ключ в Variable есть — OL побеждает DAG-conf; пишется info-лог.
    - ``"url"`` → значение ``spark.openlineage.transport.url`` из Variable.
    - ``"namespace"`` → значение ``spark.openlineage.namespace`` из Variable.
    - ``"jar"`` → ``merge_jars(current_jars, current_conf_jars, our_jar)`` через
      ``_render_jar_merge``; пустая строка, если lineage выкл или probe вернул ``False``.

    :param field: имя ключа (одно из четырёх).
    :param forced: True, если DAG форсировал включение; None — форса нет; False — форс-выключение.
    :param dag_cur: текущее значение lineage-ключа в DAG-conf (до подстановки этого макроса).
      ``None`` или ``""`` — DAG не задал. Используется для конфликтного info-лога по
      жёсткому правилу «OL побеждает по lineage-ключам» (см. §4.3).
    :return: значение для подстановки в conf; пустая строка, если lineage выкл или конфиг негоден.
    """
    cfg = _validate_cfg(_cfg())
    if cfg is None:
        return ""

    enabled = cfg.get("enabled")
    if forced is False:
        logger.info("ol_policy: lineage выключен (DAG-уровень forced=false)")
        return ""
    if forced is not True and enabled is not True:
        # _validate_cfg уже отсеял не-bool; сюда попадаем только при enabled=False.
        logger.info("ol_policy: lineage выключен (Variable.enabled=false, DAG-форса нет)")
        return ""

    spark_conf = cfg["spark_conf"]                       # type: dict[str, object]
    if field == "listener":
        value = _clean(spark_conf.get("spark.extraListeners"))
        if not value:
            # OL-listener не задан в Variable — берём DAG-conf без изменений через
            # `_render_listener_merge("", dag_cur)`. Эта утилита просто вернёт dag_cur,
            # если наш листенер пустой, либо объединит, если DAG передал свой CSV.
            return _render_listener_merge("", _resolve_local("spark.extraListeners"))
        # Наш листенер задан — мерджим с тем, что уже есть в DAG-conf.
        return _render_listener_merge(value, _resolve_local("spark.extraListeners"))
    if field == "url":
        value = _clean(spark_conf.get("spark.openlineage.transport.url"), require_scheme=True)
        if not value:
            logger.warning("ol_policy: ключ spark_conf.spark.openlineage.transport.url негодно — url не подмешан")
            return ""
        if dag_cur:
            logger.info(
                "ol_policy: spark.openlineage.transport.url в DAG-conf=%r переопределяется OL-значением=%r",
                dag_cur, value,
            )
        return value
    if field == "namespace":
        value = _clean(spark_conf.get("spark.openlineage.namespace"))
        if not value:
            logger.warning("ol_policy: ключ spark_conf.spark.openlineage.namespace не задано — namespace не подмешан")
            return ""
        if dag_cur:
            logger.info(
                "ol_policy: spark.openlineage.namespace в DAG-conf=%r переопределяется OL-значением=%r",
                dag_cur, value,
            )
        return value
    if field == "jar":
        return _resolve_jar(spark_conf, cfg)
    return ""


def _resolve_jar(spark_conf: dict[str, object], cfg: dict[str, object]) -> str:
    """Probe URI и возврат ``merge_jars``-результата. Пустая строка при любом отказе + warning.

    Мемо ``jar_available`` живёт на процессе воркера (как раньше) — повторный запуск таски
    с тем же URI не идёт в сеть.
    """
    jar_uri_obj = cfg.get("openlineage_jar")
    jar_uri = jar_uri_obj.strip() if isinstance(jar_uri_obj, str) else ""
    if not jar_uri:
        logger.warning("ol_policy: openlineage_jar не задан — jar не подмешан")
        return ""
    path = jar_path(jar_uri)
    if path is None:
        logger.warning("ol_policy: openlineage_jar задано без схемы или без пути (%s) — jar не подмешан", jar_uri)
        return ""
    if not jar_available(jar_uri, path):
        logger.warning("ol_policy: openlineage_jar не подтверждён в HDFS (%s) — jar не подмешан", jar_uri)
        return ""
    return _render_jar_merge(jar_uri)


def _render_jar_merge(our_jar: str) -> str:
    """Склеить текущие каналы jar'ов с нашим через ``merge_jars``.

    ``current_jars`` и ``current_conf_jars`` — текущие значения ``jars`` атрибута оператора
    и ``conf["spark.jars"]`` **до** подстановки результата этого макроса. Доступ через
    Jinja-локалы (``template_local``). Если локал недоступен — ``None``, ``merge_jars`` это
    уже умеет (``utils._jar_items``).
    """
    current_jars = _resolve_local("jars")
    current_conf_jars = _resolve_local("spark.jars")
    merged = merge_jars(current_jars, current_conf_jars, our_jar)
    logger.info("ol_policy: spark.jars мердж: %s", merged)
    return merged


def _render_listener_merge(our_listener: str, dag_cur: str | None) -> str:
    """Склеить CSV-лист DAG-уровня с одиночным классом OL через ``merge_listeners``.

    Жёсткое правило: «экстра листенерс тоже должен мерджится с тем что уже передано в даге».
    Дедуп сохраняет порядок: DAG-listener'ы идут первыми, OL-listener последним (если его
    в списке ещё нет). Если ни то, ни другое — пустая строка (Spark игнорирует).
    Если только DAG — возвращаем DAG как есть. Если только OL — возвращаем OL.
    """
    merged = merge_listeners(dag_cur, our_listener)
    logger.info("ol_policy: spark.extraListeners мердж: %s", merged)
    return merged


def _resolve_local(name: str) -> str | None:
    """Текущее значение ``name`` из Jinja template_local. None если локала нет.

    Обёртка нужна, чтобы ``_render_jar_merge`` оставался юнит-тестируемым без живого Jinja.
    В тестах мокается через monkeypatch ``ol_policy._resolve_local``.
    """
    from jinja2.runtime import Undefined
    try:
        from airflow.plugins_manager import get_template_locals   # type: ignore
    except Exception:
        return None
    try:
        value = get_template_locals().get(name)
    except (KeyError, AttributeError, Undefined):
        return None
    return value if isinstance(value, str) else None
```

**`_cfg` остаётся чистым reader'ом** — без изменений по структуре.

**`_validate_cfg` — единственная точка, которая пишет один warning со списком недостающих полей.**
Гарантия «конфиг переменной нужно один раз проверить при триггере» — она: один вызов на процесс
воркера, потому что `_cfg` под `lru_cache(maxsize=1)`. Сами `warn_once`-ы по полям удаляются —
`_validate_cfg` агрегирует их в одно сообщение.

**`ol_macro` всегда возвращает строку.** Jinja в conf получает либо годное значение, либо `""`;
исключений из макроса не бывает.

**Никаких silent-веток.** Каждое решение (вкл/выкл по `forced`, конфиг негоден, jar не найден,
URL негоден) пишет info- или warning-лог **на каждом вызове `ol_macro`**. Дедупликация
`warn_once` остаётся **только** для warning'а о неполной Variable (один на процесс через
`_validate_cfg`); info-логи о включении/выключении lineage **не дедуплицируются** — таска,
поведение которой молча отличается, это главный кандидат на расследование.

### 4.3 Мердж на рендере

**`spark.jars`** — мердж **объединением** (дедуп с сохранением порядка). DAG-jar'ы всегда
остаются, наш jar добавляется, если probe подтвердил URI. `_render_jar_merge` делает именно
это через `merge_jars(operator.jars, conf["spark.jars"], our_jar)` — жёсткое правило:
«джарник важно чтобы мерджился в джарники переданные дагом а не просто переопределял все».

**`spark.extraListeners`** — это **CSV-список**, а не скаляр. Семантика «OL мерджится с
DAG-listener'ами»:

- Источники: `_resolve_local("spark.extraListeners")` (текущее значение в DAG-conf, до
  рендера), результат `ol_macro('listener')` (значение из Variable, одиночный класс).
- Объединение через ту же утилиту, что `spark.jars` — `merge_listeners(dag_cur, ol_list)`
  (новая функция в `utils.py`, рядом с `merge_jars`): сплит по запятой, дедуп, сохранение
  порядка (DAG-listener'ы идут первыми, OL-listener — последним, если его там ещё нет).
- Если ни в Variable, ни в DAG-conf ничего нет — пустая строка (Spark игнорирует
  пустые `spark.extraListeners` через `Utils.stringToSeq` — грounding §2).
- Результат пишется в `conf["spark.extraListeners"]` через тот же макрос:
  `conf["spark.extraListeners"] = "{{ macros.ol_macro('listener') }}"`.

**`spark.openlineage.transport.url`** и **`spark.openlineage.namespace`** — скаляры; по ним
**OL побеждает по lineage-ключам** (жёсткое правило: «если данный параметр конфа задан в
даге, то опенлинедж его переопределяет если включён»). Реализация — через порядок рендера
Jinja, без отдельной функции:

1. DAG передаёт оператору `conf={"spark.openlineage.transport.url": "dag-url"}` — Airflow
   кладёт это в `task.conf` **до** рендера Jinja.
2. Jinja рендерит **каждое значение** в `template_fields` (включая `conf`). URL/namespace
   Airflow рендерит выражением `{{ macros.ol_macro('url') }}` (или `'namespace'`). Запись
   по тому же ключу `conf["spark.openlineage.transport.url"]` **переписывает** DAG-уровень.
3. Итоговое `conf["spark.openlineage.transport.url"]` = результат `ol_macro('url')` =
   значение из Variable (если есть). DAG-conf-значение того же ключа — **затёрто** OL.

Поведение по веткам (с журналированием через `ol_macro(field, dag_cur=…)`, где `dag_cur` —
текущее значение ключа в DAG-conf, читаемое через `_resolve_local`):

- **Есть значение в Variable, нет в DAG-conf** (`dag_cur=None`/`""`) → OL-значение попадает
  в conf. Никакого конфликта; info «`spark.openlineage.transport.url` подмешан».
- **Есть значение в Variable, есть в DAG-conf** → **OL побеждает** (через перезапись при
  рендере Jinja); info «`spark.openlineage.transport.url` в DAG-conf=… переопределяется
  OL-значением=…».
- **Нет значения в Variable, есть в DAG-conf** → `ol_macro` возвращает `""`; запись `""`
  по ключу **снова перепишет** DAG-conf на пустую строку. Это сделано намеренно: политика
  гарантирует, что URL/namespace lineage идёт на наш Marquez, либо не работает — не
  допускается «тихий lineage на чужом URL». Пишется warning «`spark.openlineage.transport.url`
  не задан в Variable — lineage-ключ очищен в conf».
- **Нет значения в Variable, нет в DAG-conf** → `ol_macro` возвращает `""`; запись пустой
  строки безвредна.

**Не-lineage ключи `spark_conf`** (например, `spark.openlineage.columnLineage.datasetLineageEnabled`)
в этой итерации **не пробрасываются** — отдельная итерация дизайна. Сейчас они остаются в
Variable, но в conf не попадают.

### 4.4 Конфликт ключей: кто кого переопределяет

| Ситуация | Результат | Лог |
| --- | --- | --- |
| `spark_conf.spark.extraListeners` задан, DAG-conf **не задал** `spark.extraListeners` | только OL-listener в conf | info «`spark.extraListeners` подмешан=OL» |
| `spark_conf.spark.extraListeners` задан, DAG-conf задал свой CSV | **DAG-listener'ы + OL-listener** (дедуп, порядок) | info «`spark.extraListeners` мердж: `<dag>,<OL>`» |
| `spark_conf.spark.extraListeners` **не задан**, DAG-conf задал свой CSV | только DAG-listener'ы в conf | info «`spark.extraListeners` без изменений: `<dag>`» |
| `spark_conf.spark.extraListeners` **не задан**, DAG-conf не задал | пустая строка | (ничего — обычное отсутствие) |
| `spark_conf.spark.extraListeners` не строка (число/список/etc.) | пустая строка | warning «`spark.extraListeners` негодно» |
| `spark_conf.spark.openlineage.transport.url` задан, **но не начинается с `http(s)://`** | пустая строка | warning «`…transport.url` негодно» |
| `spark_conf.spark.openlineage.transport.url` задан и валиден, **DAG-conf свой** | **OL побеждает** | info «`…transport.url` в DAG-conf=… переопределяется OL-значением=…» |
| `spark_conf.spark.openlineage.transport.url` задан и валиден, DAG-conf не задал | OL-значение в conf | (ничего — обычное применение без конфликта) |
| `spark_conf` целиком **не dict** (строка, число, список, отсутствует) | lineage выкл | warning (один на процесс, от `_validate_cfg`) |
| Variable негодна (любое поле) | lineage выкл | warning (один на процесс, от `_validate_cfg`) со списком полей |
| `enabled: true`, всё валидно, `openlineage_jar` валиден, **probe `False`** | jar не подмешан | warning «`openlineage_jar` не подтверждён в HDFS»; listener/url/namespace могут попасть в conf, но джоба упадёт на `ClassNotFoundException` — **это и есть тот отказ, против которого зонд**. См. §6 |
| DAG задал `jars="a.jar"` или `conf["spark.jars"]="a.jar"`, OL-jar валиден | **`a.jar + openlineage-jar`** (объединение, дедуп) | info «`spark.jars` мердж: `<a.jar>,<openlineage-jar>`» |
| DAG не задал `jars`/`conf["spark.jars"]`, OL-jar валиден | только `<openlineage-jar>` | info «`spark.jars` подмешан: `<openlineage-jar>`» |

### 4.5 Таблица истинности (рендер)

| `forced` | `enabled` | Variable валидна | listener | OL-jar (probe) | Результат | Лог |
| --- | --- | --- | --- | --- | --- | --- |
| `False` | любое | любое | любое | любое | **выкл** | info «DAG-уровень forced=false» |
| `None` | `False` | любое | любое | любое | **выкл** | info «Variable.enabled=false, DAG-форса нет» |
| `None` | не `bool` / нет | — | — | — | **выкл** | warning «Variable неполна: enabled (bool)» (один на процесс) |
| `None` или `True` | `True` | не dict / нет | — | — | **выкл** | warning «Variable неполна: spark_conf (object)» (один на процесс) |
| `None` или `True` | `True` | dict | нет | — | **выкл** | warning «`spark.extraListeners` не задан в Variable» |
| `None` или `True` | `True` | dict | да, url негоден | — | **выкл** | warning «`spark.openlineage.transport.url` негодно» |
| `None` или `True` | `True` | dict | да, всё годно | негоден/пуст | **выкл** | warning «`openlineage_jar` не задан» |
| `None` или `True` | `True` | dict | да, всё годно | валиден, probe `False` | **выкл по jar'у** | warning «`openlineage_jar` не подтверждён в HDFS» |
| `None` или `True` | `True` | dict | да, всё годно | валиден, probe `True` | **вкл** | info в `_render_jar_merge` |
| `True` | любое (включая негодный enabled) | dict | да | валиден | **вкл через forced** | info «lineage включён через forced=True» |

**Ключевые правки относительно Ревизии 1 этой спеки** (по жёстким правилам пользователя):

- **OL побеждает DAG-conf** по lineage-ключам.
- **Jar мерджится с DAG-jars** через `merge_jars` (никогда не затирает).
- **Нет silent-веток**: каждая строка таблицы — info или warning. Никаких «выкл, молча».

## 5. Контракт конфигурации

### 5.1 Airflow Variable `openlineage_config` (JSON)

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

**Изменения относительно предка:**

1. **`url` и `namespace` удалены с верхнего уровня**, переехали под `spark_conf` с префиксами
   `spark.openlineage.transport.url` / `spark.openlineage.namespace`.
2. **`spark_conf` — dict** произвольных `spark.*` ключей. Политика читает **ровно три** lineage-ключа
   из них: `spark.extraListeners`, `spark.openlineage.transport.url`, `spark.openlineage.namespace`.
   Остальные `spark_conf.*` (например, `spark.openlineage.columnLineage.datasetLineageEnabled`)
   **не пробрасываются** в этой итерации — отдельный дизайн.
3. **`openlineage_jar` — единственный источник URI openlineage-spark jar'а.** Заменяет переменную
   окружения `OPENLINEAGE_JAR`.

**Секреты в conf не попадают:** политика подставляет только `spark.openlineage.transport.url`.
Если в `spark_conf` есть ключ с текстом `auth` (`spark.openlineage.transport.auth`,
`spark.openlineage.auth` и т.п.) — пишется warning «не поддерживается» (как сегодня `auth`
верхнего уровня в предке).

### 5.2 Удаляемые переменные окружения

| Переменная | Замена |
| --- | --- |
| `OPENLINEAGE_JAR` | `Variable.openlineage_config["openlineage_jar"]` |
| `OPENLINEAGE_URL` | `Variable.openlineage_config["spark_conf"]["spark.openlineage.transport.url"]` |
| `OPENLINEAGE_NAMESPACE` | `Variable.openlineage_config["spark_conf"]["spark.openlineage.namespace"]` |
| `OPENLINEAGE_CONFIG_RESEED` | **остаётся** — аварийный рычаг пересева Variable значениями из окружения |

Из `env_example` строки `OPENLINEAGE_URL`/`OPENLINEAGE_NAMESPACE`/`OPENLINEAGE_JAR` удаляются.
`OPENLINEAGE_CONFIG_RESEED` остаётся с комментарием «принудительный пересев Variable значениями
из окружения — использовать только при миграции; после первого старта конфиг лайниджа в Variable».

`docker-compose.yml` — соответствующие ключи `environment.airflow.OPENLINEAGE_*` удаляются;
`OPENLINEAGE_CONFIG_RESEED` остаётся со значением по умолчанию `false`.

### 5.3 Тумблер из DAG'а (без изменений)

Иерархия `task.params` → `dag.params` → Variable сохраняется. Определение «форс-выключение сильнее
гейта чужого листенера» сохраняется. Нейтральные рекомендации (`Param(None, type=["null", "boolean"])`)
сохраняются.

### 5.4 Источник listener'а

`spark.extraListeners` **не задаётся** политикой хардкодом. Политика подставляет этот ключ, только
если он пришёл из Variable либо из DAG-conf. Никакого `LISTENER = "io.openlineage...."` в коде
политики больше нет — переменная `LISTENER` удаляется. Единственное место, где полное имя класса
встречается в репозитории после рефактора — `start-airflow.sh` (сидинг Variable) и `README.md`
(документация формата).

`apache-airflow-providers-openlineage` 1.11.0 в облачной среде **не** подставляет listener за
политику — `provider.yaml` подтверждает отсутствие опций инъекции в Spark-conf.

## 6. Зонд jar в HDFS — единственное изменение: точка вызова

Аппарат `jar_available` / `_probe` / `_probe_worker` / `_query_endpoint` / `_is_standby` /
`jar_path` / `NoEndpointsError` переносится без правок. Изменения ровно две:

1. **Точка вызова**: `inject_openlineage` → `ol_macro('jar')` через `_resolve_jar`.
2. **Источник URI**: `os.environ["OPENLINEAGE_JAR"]` → `cfg["openlineage_jar"]` из Variable.

Всё остальное — дедлайн 5 с, мемо по URI с TTL 300 с, демон-поток, StandbyException-ветка — без
изменений. `warn_once` по трём текстам ошибок (`jar-unset`, `jar-malformed`, `jar-absent`)
заменяется на обычные `logger.warning` (жёсткое правило: «**всегда лог**»).

**Probe на рендере под дедлайном 5 с.** Зонд съедает ≤5 с бюджета на запуск таски (мемо на
процесс воркера сводит повторные таски к нулю запросов в сеть). Это укладывается в
`SPARK_SUBMIT_DEADLINE_SEC` (35 с) с запасом. Расписание DAG-file-processor'а от зонда больше
не зависит — инвариант 7 предка («вклад политики в время парса ограничен сверху числом»)
**усиливается**: на парсе нет ни одного сетевого вызова.

**Сброс модульного состояния в `reset_state()`** — без изменений: `_warned.clear()`,
`_cfg.cache_clear()`, `_jar_memo.clear()`.

## 7. Сидинг Variable (без изменений в команде, изменение в формате)

`start-airflow.sh` уже сидит новый формат — правок в команде не требуется. Сборка JSON питоном,
без `--json`, идемпотентность через `airflow variables get` и `OPENLINEAGE_CONFIG_RESEED` — без
изменений. Удаляются ENV-переменные `OPENLINEAGE_URL`, `OPENLINEAGE_NAMESPACE`, `OPENLINEAGE_JAR`
из смоук-сборки — JSON собирается из уже удалённых полей, поэтому смоук-сборка сокращается до:

```python
print(json.dumps({
    "enabled": True,
    "spark_conf": {
        "spark.extraListeners": "io.openlineage.spark.agent.OpenLineageSparkListener",
        "spark.openlineage.transport.type": "http",
        "spark.openlineage.transport.url": "http://marquez:5000",
        "spark.openlineage.namespace": "hadoop-cluster",
        "spark.openlineage.columnLineage.datasetLineageEnabled": "true",
    },
    "openlineage_jar": "hdfs://namenode:9000/opt/openlineage/openlineage-spark_2.13-1.46.0.jar",
}))
```

Конкретные значения `transport.url`, `namespace`, `openlineage_jar` берутся **прямо текстом** —
больше не из ENV. Это **намеренное упрощение**: если нужно поменять — поменяй Variable в UI,
а не `.env`. Стендовые дефолты — `http://marquez:5000`, `hadoop-cluster`,
`hdfs://namenode:9000/opt/openlineage/openlineage-spark_2.13-1.46.0.jar`.

## 8. Инварианты

Предок §8 остаётся в силе полностью, со следующими правками:

- **Инвариант 6** ужесточается формулировкой: «Зеро обращений к Variable, метастору и HDFS на
  парсе **и** при подготовке шаблонов» — `Variable.get`, `urlopen`, любой `urllib.*`,
  `_probe`/`_probe_worker`/`_query_endpoint`/`_is_standby`/`jar_available` запрещены в
  `inject_openlineage`, `operator_attrs`, `_level_forced`, `lineage_forced`, `_clean`,
  `merge_jars`, `jar_path`, `passthrough_exceptions`. Единственные места — `_cfg` и
  `_resolve_jar` на рендере.
- **Инвариант 7** усиливается: на парсе **ноль сетевых вызовов**. Дедлайн 5 с перенесён с парса
  на рендер и теперь живёт в бюджете `SPARK_SUBMIT_DEADLINE_SEC` (35 с), а не в бюджете
  `[core] dag_file_processor_timeout` (50 с).
- **Инвариант 12 — Variable owns listener.** Никакого хардкода класса листенера в политике.
  Если `spark.extraListeners` не пришёл ни из Variable, ни из DAG-conf — lineage выкл + warning.
  Тест: `LISTENER` константа отсутствует в `ol_policy/__init__.py`.
- **Инвариант 13 — OL побеждает по URL/namespace.** Для `spark.openlineage.transport.url`
  и `spark.openlineage.namespace` итоговое значение в conf берётся из Variable, даже если DAG
  задал свой ключ; пишется info-лог с обоими значениями.
- **Инвариант 16 — `spark.extraListeners` мерджится.** Итоговое значение в conf —
  `merge_listeners(_resolve_local("spark.extraListeners"), our_listener)`: DAG-listener'ы
  идут первыми, OL-listener последним (если его ещё нет). Никогда не затирает DAG-listener'ы.
  Пишется info-лог с обоими источниками.
- **Инвариант 14 — jar мерджится через `merge_jars`.** Политика никогда не затирает ни
  `operator.jars`, ни `conf["spark.jars"]` целиком. Итоговое значение `spark.jars` —
  `merge_jars(operator.jars, conf["spark.jars"], our_jar)`.
- **Инвариант 15 — никаких silent-веток.** Каждое решение политики (включили lineage,
  выключили, конфиг негоден, jar не подмешан, OL-перебил-DAG-conf) пишет info- или
  warning-лог. Дедуплицируется только warning «Variable неполна» через `_validate_cfg` (один
  на процесс).

## 9. Тесты

Расширения к RED-набору предка:

- **`LISTENER` константа отсутствует.** Инвариант 12: `assert not hasattr(ol_policy, "LISTENER")`.
- **`_validate_cfg` пишет один warning со списком недостающих полей.** При Variable
  `{"enabled": true, "spark_conf": {}, "openlineage_jar": ""}` — одна запись лога со списком
  трёх полей (`spark_conf.spark.extraListeners`, `…transport.url`, `…namespace`,
  `openlineage_jar`). Дубль лога в `ol_macro` (`logger.warning` «конфиг не валиден»)
  не происходит — `_validate_cfg` уже вернул `None`.
- **`_validate_cfg` запускается один раз на процесс.** После двух вызовов `ol_macro` подряд
  счётчик вызовов `_validate_cfg` равен 1 (мемо `_cfg` + детерминизм `_validate_cfg`).
- **`ol_macro('listener')` при Variable `{enabled: true, spark_conf: {"spark.extraListeners": "com.example.X"}}`,
  `_resolve_local("spark.extraListeners")` = `None` → `"com.example.X"` + info «подмешан».**
- **`ol_macro('listener')` при Variable без `spark.extraListeners`,
  `_resolve_local("spark.extraListeners")` = `"com.example.A,com.example.B"` → `"com.example.A,com.example.B"`
  + info «без изменений».** Инвариант 16 (DAG-listener'ы не затираются).
- **`ol_macro('listener')` при Variable с `spark.extraListeners = "io.openlineage..."`,
  `_resolve_local("spark.extraListeners")` = `"com.example.A,com.example.B"` →
  `"com.example.A,com.example.B,io.openlineage..."` + info «мердж: …».** Инвариант 16.
- **`ol_macro('listener')` при Variable с `spark.extraListeners = "com.example.A"`,
  `_resolve_local("spark.extraListeners")` = `"com.example.A,com.example.B"` →
  `"com.example.A,com.example.B"` + info «мердж: …»** (OL-listener уже в DAG-CSV → дедуп, не дублируется).
- **`ol_macro('listener')` при Variable без `spark.extraListeners`,
  `_resolve_local("spark.extraListeners")` = `None` → `""`** (без лога — обычное отсутствие).
- **`ol_macro('url')` при Variable с `spark.openlineage.transport.url = "   "` (пробелы) → `""` + warning.**
- **`ol_macro('url')` при Variable с `spark.openlineage.transport.url = 5000` (число) → `""` + warning.**
- **`ol_macro('url')` при Variable с `spark.openlineage.transport.url = "marquez:5000"` (без схемы) → `""` + warning.**
- **`ol_macro('namespace')` при Variable с `spark.openlineage.namespace = "   "` → `""` + warning.**
- **`ol_macro('listener', dag_cur="dag.X")` при Variable с
  `spark.extraListeners = "ol.Y"` → возвращает `"ol.Y"` + info-лог с обоими значениями**.
  Инвариант 13.
- **`ol_macro('jar')` при Variable с `openlineage_jar = "hdfs://..."` и probe `True`,
  `_resolve_local("jars")` = `"a.jar"` → `"a.jar,<openlineage-jar>"`.** Инвариант 14.
- **`ol_macro('jar')` при Variable с `openlineage_jar = "hdfs://..."` и probe `True`,
  `_resolve_local("jars")` = `None` → `"<openlineage-jar>"`.**
- **`ol_macro('jar')` при Variable с `openlineage_jar = ""` → `""` + warning «не задано».**
- **`ol_macro('jar')` при Variable с `openlineage_jar = "/opt/x.jar"` (без схемы) → `""` + warning «без схемы».**
- **`ol_macro('jar')` при Variable с `openlineage_jar` валидным и probe `False` → `""` + warning «не подтверждён в HDFS».**
- **`ol_macro('jar')` кеширует результат probe по URI** — второй вызов с тем же URI не идёт в сеть
  (мемо `_jar_memo`).
- **`ol_macro` при Variable валидной пишет info-лог на каждом вызове.** Инвариант 15: ни одна
  ветка не молчит.
- **`inject_openlineage` НЕ пишет в атрибут `jars` оператора.** Проверка: `getattr(task, attrs.jars)`
  до и после равны. Проверяется на обеих раскладках атрибутов (4.1.1 и 4.10.0).
- **`inject_openlineage` НЕ пишет в `conf`.** Проверка: `getattr(task, attrs.conf)` до и после равны.
- **`inject_openlineage` НЕ читает `OPENLINEAGE_JAR` env.** Проверка: `os.environ` без этого ключа,
  `inject_openlineage` не бросает и только регистрирует макрос.
- **`inject_openlineage` НЕ читает Variable.** Дубль `Variable.get` валит тест при вызове; полный
  прогон `inject_openlineage` по обеим раскладкам не трогает его.
- **`inject_openlineage` НЕ вызывает `jar_available` / `urlopen`.** Дубль `urlopen` валит тест при
  вызове; полный прогон — не вызывает. Probe зовётся только из `_resolve_jar` на рендере.
- **Полный RED-набор предка** с обновлённой таблицей истинности §4.5 — все строки проходят с
  новыми полями Variable.
- **Сидинг `start-airflow.sh`** строит JSON → `Variable.get` + `json.loads` → `dict` с полями
  `enabled`, `spark_conf`, `openlineage_jar`. Регрессионный тест на формат.

Смоуки (`tests/test-policy.bat`, `tests/test-airflow.bat`):

- `test-policy.bat` без изменений.
- `test-airflow.bat` расширяется: после правки Variable на новый формат (`spark_conf.*` +
  `openlineage_jar`) следующий запуск таски подхватывает изменения без рестарта; **DAG, задавший
  свой `spark.extraListeners` в conf, всё равно получает OL-listener из Variable** (инвариант 13);
  **DAG с `conf["spark.jars"]="a.jar"` получает `a.jar + openlineage-jar` в `spark.jars`** через
  `SparkSubmitHook._build_spark_submit_command`.

## 10. Изменения по файлам

| Файл | Изменение |
| --- | --- |
| `airflow/config/ol_policy/__init__.py` | `inject_openlineage` пишет **только** макрос — `conf` и `jars` не трогает; `ol_macro` принимает четыре `field` (`listener`, `url`, `namespace`, `jar`) и зовёт `_resolve_jar` для `jar` или валидирует поле `spark_conf` для остальных; для `listener` — мердж через `_render_listener_merge` (вызывает `merge_listeners`); новая функция `_validate_cfg` агрегирует недостающие поля в один warning на процесс; новая функция `_render_jar_merge` вызывает `merge_jars` через `_resolve_local`; новая функция `_render_listener_merge` вызывает `merge_listeners` через `_resolve_local`; новая функция `_resolve_local` читает Jinja template_local; `warn_once`-ы заменены на `logger.warning`/`logger.info` (инвариант 15); константа `LISTENER` удалена |
| `airflow/config/ol_policy/logger.py` | без изменений (механизм `warn_once` остаётся для других мест, если они появятся) |
| `airflow/config/ol_policy/utils.py` | новая функция `merge_listeners(dag_cur, our_listener)`: сплит по запятой (с Jinja-сохранением, как в `_jar_items`), дедуп, порядок: DAG-listener'ы первыми, наш последним; `merge_jars` без изменений |
| `airflow/scripts/start-airflow.sh` | сборка JSON упрощается: `url`/`namespace`/`openlineage_jar` берутся как литералы (больше не из ENV); `OPENLINEAGE_*` ENV-переменные удалены из heredoc |
| `env_example` | строки `OPENLINEAGE_URL`, `OPENLINEAGE_NAMESPACE`, `OPENLINEAGE_JAR` удалены; комментарий к `OPENLINEAGE_CONFIG_RESEED` уточнён |
| `docker-compose.yml` | ключи `environment.airflow.OPENLINEAGE_URL/NAMESPACE/JAR` удалены; `OPENLINEAGE_CONFIG_RESEED=false` остаётся |
| `airflow/config/tests/test_ol_policy.py` | новые тесты §9; удалены тесты `OPENLINEAGE_JAR env`-зависимости (фикстура `jar_env` остаётся только для тех, которые проверяют отсутствие чтения env); таблица истинности §4.5 — все строки; тесты на `_validate_cfg` (один warning, один вызов на процесс); тесты на `_render_jar_merge` (мердж с `a.jar`); тесты на инвариант 13 (OL побеждает DAG-conf + info-лог) |
| `airflow/config/tests/conftest.py` | без изменений; `reset_state()` сбрасывает `_jar_memo`, `_cfg`, `_warned` (как сегодня) |
| `README.md`, `tests/README.md` | формат Variable обновлён; тумблер — без изменений; раздел «Listener» — разъяснён (Variable owns); раздел «Merger» — добавлен (инвариант 14) |
| `airflow/Dockerfile` | без изменений |
| `CHANGELOG.md` | одна запись: «cycle 1: Variable хранит `spark_conf` и `openlineage_jar`; OL побеждает DAG-conf по lineage-ключам; jar мерджится через `merge_jars`; ноль silent-веток; probe сдвинут с парса на рендер» |

## 11. Риски

- **Поведение `--jars` против `spark.jars` на стороне Spark.** `SparkSubmitArguments.loadEnvironmentArguments`
  берёт `Option(jars).orElse(sparkProperties.get(JARS.key))` — если заданы оба (`jars=...` и
  `spark.jars=...`), Spark берёт `jars` (явный `--jars` вытесняет); `spark.jars` отбрасывается
  как источник. На стенде оба источника сейчас не задаются одновременно (DAG не задаёт `jars=`);
  в облаке `apache-airflow-providers-openlineage` теоретически может добавить свой `--conf spark.jars`.
  Смоук `test-airflow.bat` закрывает это фактическим запуском.
- **`airflow variables set` из UI с не-объектным JSON.** UI сохраняет введённое поле `Val` как
  строку. Если оператор вставит не-объектный JSON (`"true"`, `5000`, `[1,2,3]`), `_validate_cfg`
  пишет warning «`Variable openlineage_config` — не JSON-объект» и возвращает `None`. Защита уже есть.
- **Probe на рендере съедает 5 с бюджета воркера.** При недостижимом NameNode запуск таски
  откладывается на 5 с. `SPARK_SUBMIT_DEADLINE_SEC=35`, остаётся 30 с на spark-submit + YARN-
  handshake. Приемлемо; смоук фиксирует время.
- **`apache-airflow-providers-openlineage` 1.11.0** в облачной среде — конкурирующий писатель
  в conf не обнаружен (проверено в предке §2). Если в будущей версии появится — поведение
  не наша забота, см. §11 предка.
- **Не перепроверено для 2.10.2:** поведение `SandboxedEnvironment` для `merge_jars` через
  `_render_jar_merge` — `get_template_env` строится на каждый рендер (`cache_size: 0`), макрос
  callable. Риск минимальный, проверяется юнит-тестом `test-render-jar-merge-jinja` на
  `DAG.get_template_env()`.
- **`spark.extraListeners` и пробелы / формат.** Spark сплитит по запятой через `Utils.stringToSeq`
  (грounding §2), фильтрует пустые и пробельные. `merge_listeners` тоже фильтрует и сплитит
  по запятой, но **сохраняет пробелы внутри токенов** — на стенде классы без пробелов,
  риск нулевой. Если понадобится — добавить `strip()` к каждому токену в `merge_listeners`.
- **Цикл 2 из §10 предка** (вынос `enabled` в DAG-уровневый override с per-DAG namespace) —
  отдельный дизайн.

## 12. Журнал ревизий

### Ревизия 2 (cycle 1 — эта)

Жёсткие правила пользователя внесены в §4–§8.

| Было (Ревизия 1) | Стало (Ревизия 2) | Почему |
| --- | --- | --- |
| DAG-conf побеждает OL по lineage-ключам (`{**spark_conf, **cur_conf}`) | **OL побеждает DAG-conf** по `spark.openlineage.transport.url`/`spark.openlineage.namespace` (порядок рендера Jinja: результат `ol_macro` переписывает ключ в `conf`; конфликтный info-лог через `ol_macro(field, dag_cur=…)`); **`spark.extraListeners` мерджится** через `merge_listeners` (DAG-listener'ы первыми, OL-listener последним, дедуп, info-лог) | «опенлайн переопределяет если включён» + «экстра листенерс тоже должен мерджится с тем что уже передано в даге» |
| Атрибут `jars` не пишется; `conf["spark.jars"]` получает только наш URI | **Мердж через `merge_jars(operator.jars, conf["spark.jars"], our_jar)`** через `_render_jar_merge` | «джарник важно чтобы мерджился в джарники переданные дагом а не просто переопределял все» |
| `warn_once` для неполной Variable и отдельных полей | **`_validate_cfg` агрегирует недостающие поля в один warning** на процесс, остальные ветки — обычные `logger.warning/info` | «конфиг переменной нужно один раз проверить при триггере если там чего не хватает» |
| Несколько «молчаливых» веток (forced=False, enabled=False, §4.4 таблица) | **Каждая ветка пишет info- или warning-лог** | «ВСЕГДА ЛОГ» |
| `inject_openlineage` пишет conf через `setattr(task, attrs.conf, …)` | `inject_openlineage` **не пишет conf** — пишет только макрос; мерджится на рендере через `_render_jar_merge` (jar) и порядок рендера Jinja (lineage-ключи) | OL-и-DAG-conf мердж требует знания исходного conf; на парсе это неизвестно безопасно |
| Новые инварианты 10 (Variable owns listener) и 11 (jar через conf) | Сохранены + добавлены **12 (Variable owns listener)**, **13 (OL побеждает по URL/namespace)**, **14 (jar мерджится)**, **15 (всегда лог)**, **16 (extraListeners мерджится)** | Жёсткие правила |

### Ревизия 1 (cycle 1 — предыдущая)

| Было | Стало | Почему |
| --- | --- | --- |
| Variable `{enabled, url, namespace}` — политика знает три ключа и подставляет их | Variable `{enabled, spark_conf: dict, openlineage_jar: str}` — `spark_conf` целиком мерджится в conf, jar URI живёт в Variable | сидинг в `start-airflow.sh` уже пишет новый формат, код его не понимал — рассогласование |
| `OPENLINEAGE_JAR` env, читается на парсе в `inject_openlineage` | `openlineage_jar` в Variable, читается на рендере в `ol_macro('jar')` | требование: Variable не читается на парсе (scheduler / dag bag) |
| Probe HDFS запускается на парсе | Probe запускается на рендере через `_resolve_jar` | парс не должен ходить в сеть; единственная причина probe на парсе была — источник URI в env, которого больше нет |
| `LISTENER = "io.openlineage.spark.agent.OpenLineageSparkListener"` хардкод в `ol_policy/__init__.py` | `LISTENER` удалён; класс берётся из `spark_conf["spark.extraListeners"]` либо из DAG-conf | цель 3: Variable owns listener |
| `ol_macro` принимает три `field` (`listener`, `url`, `namespace`) | `ol_macro` принимает четыре `field` (`listener`, `url`, `namespace`, `jar`) | jar URI теперь часть контракта Variable |
| Инвариант 7: «вклад политики в время парса ограничен сверху числом» | Инвариант 7: «на парсе **ноль сетевых вызовов**; дедлайн 5 с перенесён на рендер» | probe больше не на парсе |
