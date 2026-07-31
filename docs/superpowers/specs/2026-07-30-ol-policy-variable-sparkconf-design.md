# Cycle 1: Variable хранит `spark_conf` целиком и `openlineage_jar`, без чтения Variable на парсе

**Дата:** 2026-07-30, Ревизия 3 — 2026-07-31
**Статус:** Ревизия 3 утверждена (brainstorming), готова к плану реализации. Ревизия 2 была
реализована и откачена — см. §12
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
| `--jars` оператора в `SparkSubmitArguments.loadEnvironmentArguments` стоит **раньше** `spark.jars`: `Option(jars).orElse(sparkProperties.get(JARS.key))` — явный `--jars` вытесняет `spark.jars` как источник | `core/.../deploy/SparkSubmitArguments.scala` (тег `v3.5.2`) | **исправлено в Ревизии 3**: итог `merge_jars` пишется в **атрибут `jars`** оператора, а не в `conf["spark.jars"]`. Если DAG задал `jars=`, то `conf["spark.jars"]` Spark не посмотрит вовсе — запись мерджа туда потеряла бы наш jar |
| Пустой `spark.extraListeners` безопасен: `.stringConf.toSequence.createOptional`, `Utils.stringToSeq` фильтрует пустые, `loadExtensions` делает `flatMap` | `core/.../internal/config/package.scala:1423-1428`, `core/.../util/Utils.scala:2754-2772` (тег `v3.5.2`) | ничего |
| Полное имя класса листенера — `io.openlineage.spark.agent.OpenLineageSparkListener`; сопутствующие ключи `spark.openlineage.transport.type`/`.url`/`spark.openlineage.namespace` | доки OpenLineage | имя **больше не хардкодится** в политике — Variable owns listener, §4 |
| Зонд HDFS через демон-поток, мемо по URI с TTL 300 с, дедлайн 5 с — перенесён из §6.1 предка **без изменений**: единственный stdlib-примитив с гарантированным дедлайном, покрывает `getaddrinfo` (которому таймаут сокета не передан) | предок §6.1 | **место вызова**: с парса (`inject_openlineage`) → на рендер (`ol_macro` → `_resolve_jar`), §4 |
| `airflow variables set --json` означает сериализовать, а не «значение уже JSON»: `Variable.set(key, value, serialize_json=args.json)` при `serialize_json=True` делает `json.dumps(value, indent=2)` | `airflow/cli/commands/variable_command.py::variables_set`; `airflow/models/variable.py::set` | ничего — `start-airflow.sh` уже пишет без `--json` |
| `airflow variables get` при отсутствии ключа поднимает `SystemExit` — ненулевой код возврата, пригоден для идемпотентного сидинга | `airflow/cli/commands/variable_command.py:41-51` | ничего |
| `TaskInstance.render_template` рекурсивно рендерит значения `dict` в `template_fields`; `conf` оператора входит в `template_fields` | `airflow/models/taskinstance.py:1531`; `spark_submit.py:75-79` | новое: на рендере политика вызывает `_cfg` (один раз на процесс), `_validate_cfg` (один раз на процесс), probe jar'а (один раз на URI) |
| `AbstractOperator._do_render_template_fields` делает `setattr(parent, attr_name, rendered_content)` **после** рендера поля — на момент рендера исходное значение атрибута ещё цело | `airflow/models/abstractoperator.py` (2.6.3, прочитано `inspect.getsource` 2026-07-31) | справочно: в Ревизии 3 не используется — мердж собирается на парсе |
| Возврат макроса из `user_defined_macros` **не рендерится повторно** | [FAQ — Macros defined in user_defined_macros are not recursively rendered](https://airflow.apache.org/docs/apache-airflow/stable/faq.html) | новое: макрос обязан отдавать готовое значение; собственная Jinja DAG'а должна остаться **в строке**, а не проходить через возврат макроса |
| `DAG.get_template_env()` строит `SandboxedEnvironment` с `cache_size: 0` — каждый рендер заново | `airflow/models/dag.py::get_template_env` | ничего |
| `ParamsDict.__getitem__` бросает `ParamValidationError`; `MutableMapping.get` её не ловит | `airflow/models/param.py` | ничего — `_level_forced` уже под `try/except` |
| ~~Jinja `template_local` позволяет достать значение текущего атрибута оператора / ключа conf~~ — **ФАКТ ОПРОВЕРГНУТ** | проверка 2026-07-31 на установленной Airflow 2.6.3: `hasattr(airflow.plugins_manager, "get_template_locals") is False`; в доках Airflow stable про template locals нет ничего; цитировавшийся раздел Jinja «Context locals» описывает `Context`/`derived`, а не чтение рендерящегося атрибута | **Ревизия 3 удаляет `_resolve_local` целиком.** Функция всегда возвращала бы `None` в проде: мердж listener'ов выродился бы в OL-only, `spark.jars` DAG'а был бы затёрт. Тесты этого не ловили — они монкипатчили `_resolve_local` |
| `jinja2.pass_context` существует (3.1.2) и передаёт `Context` первым аргументом; `DAG.get_template_env` кладёт `user_defined_macros` в `env.globals`; `TaskInstance.get_template_context` содержит `"task": task` | [Jinja API — pass_context](https://jinja.palletsprojects.com/en/stable/api); проверено запуском 2026-07-31 | рассмотрено как замена `_resolve_local` и **отклонено**: возврат макроса не рендерится повторно, поэтому DAG с `{{ var.value.x }}` в `jars` получил бы сырой текст. См. §4.3 |

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
   перезаписывается нашим. Два канала (`jars` и `conf["spark.jars"]`) объединяются на парсе
   через `merge_jars`, итог пишется в **атрибут `jars`**; наш URI дописывает макрос на рендере.
   Дополнительно: OL-`spark_conf` по **lineage-ключам** мерджится **поверх** DAG-conf
   (`OL побеждает по ключам lineage` — жёсткое правило); не-lineage `spark.*` остаются за
   пользователем.

## 4. Архитектура

### 4.1 Изменения в `inject_openlineage` (парс)

**Парс остаётся писателем `conf` и атрибута `jars` — но пишет только строки, не значения.**
Ревизия 2 предлагала «только регистрируем макрос»; это нежизнеспособно: если политика не
положит `{{ __openlineage_v1(...) }}` в conf таски, макросу неоткуда взяться, и лайнидж
заработает только у DAG'ов, которые вписали вызов руками. Это противоречит цели «без правок
в DAG'ах».

```python
_UNSAFE_FOR_LITERAL = ("{{", "{%", "'", '"')


def _dag_channel(value: object) -> tuple[str, str | None]:
    """Каким каналом отдать DAG-значение макросу.

    :param value: значение из conf/атрибута таски, каким его задал DAG.
    :return: пара ``(prefix, dag_cur)``. ``prefix`` дописывается в строку перед
        вызовом макроса; ``dag_cur`` уходит третьим аргументом макроса.
    """
    text = value.strip() if isinstance(value, str) else ""
    if not text:
        return "", ""                                   # DAG молчит — макрос вернёт значение без разделителя
    if any(marker in text for marker in _UNSAFE_FOR_LITERAL):
        return text, None                               # своя Jinja/кавычки — текст остаётся в строке
    return "", text                                     # безопасный литерал — макрос смерджит и дедуплицирует


def _macro_call(field: str, forced: str, dag_cur: str | None) -> str:
    """Текст вызова макроса для подстановки в conf."""
    literal = "none" if dag_cur is None else f"'{dag_cur}'"
    return f"{{{{ {MACRO}('{field}', {forced}, {literal}) }}}}"


def inject_openlineage(task: object) -> None:
    # гейты 1-4 без изменений: operator_attrs, lineage_forced, dag, MACRO
    macros = dict(getattr(dag, "user_defined_macros", None) or {})
    macros[MACRO] = ol_macro
    dag.user_defined_macros = macros

    forced = "true" if lineage_forced(task) is True else "none"
    cur_conf = dict(getattr(task, attrs.conf) or {})

    listener_prefix, listener_cur = _dag_channel(cur_conf.get("spark.extraListeners"))
    # оба канала jar'ов известны на парсе — склеиваем их до макроса
    jars_prefix, jars_cur = _dag_channel(
        utils.merge_jars(getattr(task, attrs.jars), cur_conf.get("spark.jars"))
    )
    # скаляры: prefix отбрасывается — OL перекрывает DAG-значение, литерал нужен только логу
    _, url_cur = _dag_channel(cur_conf.get("spark.openlineage.transport.url"))
    _, ns_cur = _dag_channel(cur_conf.get("spark.openlineage.namespace"))

    setattr(task, attrs.conf, {
        **cur_conf,
        "spark.extraListeners": listener_prefix + _macro_call("listener", forced, listener_cur),
        "spark.openlineage.transport.type": "http",
        "spark.openlineage.transport.url": _macro_call("url", forced, url_cur),
        "spark.openlineage.namespace": _macro_call("namespace", forced, ns_cur),
        "spark.openlineage.columnLineage.datasetLineageEnabled": "true",
    })
    setattr(task, attrs.jars, jars_prefix + _macro_call("jar", forced, jars_cur))
```

Ключевое:

- **Ноль обращений к Variable, метастору и HDFS.** На парсе только чтение атрибутов таски и
  сборка строк. `os.environ["OPENLINEAGE_JAR"]`, `jar_path`, `jar_available` из
  `inject_openlineage` уходят целиком — probe живёт на рендере.
- **Итог jar-мерджа пишется в атрибут `jars`, не в `conf["spark.jars"]`** — иначе при заданном
  DAG'ом `jars=` Spark наш jar не увидит (§2, `Option(jars).orElse(...)`).
- **OL побеждает по lineage-ключам** буквально: наши четыре ключа пишутся **после** `**cur_conf`,
  то есть перекрывают DAG-значение. Остальные ключи DAG-conf не трогаются.
- **DAG-значения не теряются**: они либо переданы макросу литералом (`dag_listener` / `dag_jars`),
  либо остаются в строке перед вызовом макроса (`prefix`). См. §4.3.

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


@functools.lru_cache(maxsize=1)
def _validate_cfg() -> dict[str, object] | None:
    """Валидирует Variable **один раз на процесс**: список недостающих полей — одним warning'ом.

    Функция **не принимает аргументов** и сама зовёт ``_cfg()``. Вариант из Ревизии 2
    (``_validate_cfg(cfg: dict)`` под ``lru_cache``) неработоспособен: ``lru_cache``
    хэширует аргументы, а dict нехэшируем — первый же вызов дал бы
    ``TypeError: unhashable type: 'dict'``.

    ``_cfg`` уже мемоизирован; ``_validate_cfg`` под тем же ``lru_cache(maxsize=1)`` даёт
    один вызов на процесс воркера — иначе aggregated warning «Variable неполна» сработал
    бы на каждом из четырёх вызовов ``ol_macro`` за один рендер. Если Variable негоден,
    возвращает ``None``; ``ol_macro`` в этом случае повторный warning не пишет.

    Это и есть «**один раз проверить при триггере**»: одна валидация — один warning.

    :return: cfg из ``_cfg``, либо None, если он непригоден.
    """
    cfg = _cfg()
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

    - ``"listener"`` → значение из Variable, оформленное под канал ``dag_cur`` через ``_emit``
      (мердж с дедупом, либо значение как есть, либо значение с ведущей запятой).
    - ``"url"`` → значение ``spark.openlineage.transport.url`` из Variable; при пустом
      результате пишется warning «url не подмешан» и возвращается ``""``.
    - ``"namespace"`` → значение ``spark.openlineage.namespace`` из Variable; аналогично.
    - ``"jar"`` → URI из Variable после probe, оформленный тем же ``_emit``; пустая строка,
      если lineage выкл или probe вернул ``False``.

    :param field: имя ключа (одно из четырёх).
    :param forced: True, если DAG форсировал включение; None — форса нет; False — форс-выключение.
    :param dag_cur: DAG-значение того же ключа, переданное **парсом**. Три состояния:

      * ``""`` — DAG ключ не задавал. Макрос возвращает своё значение без разделителя.
      * строка — безопасный литерал DAG-значения. Макрос возвращает полный мердж с дедупом
        (``merge_listeners`` / ``merge_jars``), DAG-значения идут первыми.
      * ``None`` — DAG-значение содержит свою Jinja или кавычки и потому осталось **текстом
        в строке слева** от вызова макроса. Макрос возвращает своё значение **с ведущей
        запятой**; дедуп в этом случае невозможен (warning на парсе).

      Такой контракт делает разделитель ответственностью макроса: висячей запятой не
      возникает ни при выключенном lineage, ни при пустом DAG-значении.

      Это для веток ``listener`` и ``jar`` (списки). Для скаляров ``url`` и ``namespace``
      ``dag_cur`` — **только материал конфликтного info-лога**: значение возвращается
      целиком, разделителя нет, DAG-значение перекрыто на парсе.
    :return: значение для подстановки в conf; пустая строка, если lineage выкл или конфиг негоден.
    """
    cfg = _validate_cfg()
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
            logger.warning("ol_policy: spark_conf.spark.extraListeners не задан — listener не подмешан")
            return ""
        return _emit(value, dag_cur, utils.merge_listeners, "spark.extraListeners")
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
        return _resolve_jar(cfg, dag_cur)
    return ""


def _emit(value: str, dag_cur: str | None, merge: Callable[[object, object], str], key: str) -> str:
    """Оформить наше значение под тот канал, которым пришло DAG-значение.

    Единственное место, где решается разделитель. Контракт ``dag_cur`` — см. ``ol_macro``.

    :param value: наше значение из Variable (уже проверено ``_clean``).
    :param dag_cur: ``""`` / литерал / ``None`` — канал, выбранный парсом.
    :param merge: ``utils.merge_listeners`` либо ``utils.merge_jars``.
    :param key: имя ключа для лога.
    :return: строка для подстановки.
    """
    if dag_cur is None:                       # текст DAG'а стоит слева — дописываем через запятую
        logger.info("ol_policy: %s дописан к DAG-значению (дедуп невозможен): %s", key, value)
        return f",{value}"
    if not dag_cur:                           # DAG молчал — отдаём как есть
        logger.info("ol_policy: %s подмешан: %s", key, value)
        return value
    merged = merge(dag_cur, value)            # безопасный литерал — полный мердж с дедупом
    logger.info("ol_policy: %s мердж: %s", key, merged)
    return merged


def _resolve_jar(cfg: dict[str, object], dag_cur: str | None) -> str:
    """Probe URI и оформление результата. Пустая строка при любом отказе + warning.

    Мемо ``jar_available`` живёт на процессе воркера — повторный запуск таски с тем же URI
    в сеть не идёт.

    :param cfg: разобранный Variable из ``_validate_cfg``.
    :param dag_cur: канал DAG-значения jar'ов (см. ``ol_macro``).
    :return: строка для подстановки в атрибут ``jars``; ``""``, если probe отказал.
    """
    jar_uri_obj = cfg.get("openlineage_jar")
    jar_uri = jar_uri_obj.strip() if isinstance(jar_uri_obj, str) else ""
    if not jar_uri:
        logger.warning("ol_policy: openlineage_jar не задан — jar не подмешан")
        return ""
    path = jar_path(jar_uri)
    if path is None:
        logger.warning("ol_policy: openlineage_jar задан без схемы или без пути (%s) — jar не подмешан", jar_uri)
        return ""
    if not jar_available(jar_uri, path):
        logger.warning("ol_policy: openlineage_jar не подтверждён в HDFS (%s) — jar не подмешан", jar_uri)
        return ""
    return _emit(jar_uri, dag_cur, utils.merge_jars, "spark.jars")
```

**`_resolve_local` удалена.** Её единственный источник данных — несуществующий API (§2).
Текущее DAG-значение приходит в макрос параметром `dag_cur`, который заполняет парс.

**Тексты сообщений пишутся inline** в местах вызова логгера — отдельных `_MSG_*` констант
не заводим. Дедупликация `warn_once` опирается на ключ-кортеж (`("no-var",)`,
`("bad-shape",)`, …), а не на текст, поэтому вынос текстов ничего не давал. Требование
«тексты причин попарно различимы» проверяется тестом по фактическому `caplog`: прогнать
сценарии и сравнить записи, а не список констант.

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

### 4.3 Мердж: собирается на парсе, значение приходит с рендера

Разделение ответственности буквальное:

| Кто | Что знает | Что делает |
| --- | --- | --- |
| **парс** | DAG-значения `jars`, `conf["spark.jars"]`, `conf["spark.extraListeners"]` | склеивает два jar-канала, выбирает канал передачи (`_dag_channel`), собирает строку с вызовом макроса |
| **рендер** | Variable, результат probe | отдаёт своё значение, оформленное под выбранный канал (`_emit`) |

Почему не наоборот. Полный мердж на рендере требует, чтобы макрос увидел исходное
DAG-значение. Читать его на рендере нечем: `get_template_locals` не существует (§2), а
`pass_context` + `context["task"]` упирается в то, что возврат макроса **не рендерится
повторно** — DAG с `{{ var.value.x }}` в `jars` получил бы этот текст сырым в
`spark-submit`. Поэтому DAG-значение остаётся в строке, и мердж собирает парс.

**`spark.jars`** — оба канала DAG'а (`operator.jars` и `conf["spark.jars"]`) склеиваются на
парсе через `merge_jars`, итог пишется в **атрибут `jars`** (§2: явный `--jars` вытесняет
`spark.jars`). Наш URI дописывается макросом, если probe подтвердил его на рендере. Жёсткое
правило «джарник мерджится, а не переопределяет» выполняется: DAG-jar'ы стоят в строке до
вызова макроса и не зависят от того, что вернёт Variable.

**`spark.extraListeners`** — CSV-список. DAG-CSV известен парсу; OL-класс приходит из
Variable. При безопасном DAG-значении макрос получает его литералом и делает
`merge_listeners` с дедупом — это защищает от двух инстансов одного листенера (иначе
дублирующиеся OL-события). При DAG-значении со своей Jinja дедуп невозможен, парс пишет
warning.

**`spark.openlineage.transport.url`** и **`spark.openlineage.namespace`** — скаляры, **OL
побеждает**. Механизм не «порядок рендера Jinja» (это фикция: если DAG положил литерал,
рендерить нечего), а порядок ключей в словаре на парсе: наши ключи пишутся после `**cur_conf`
и перекрывают DAG-значение.

У этих двух веток `dag_cur` играет **другую роль** — он не канал мерджа, а материал для
конфликтного info-лога: скаляр всегда возвращается целиком, разделителя нет. Парс отдаёт
литерал DAG-значения (`""` — DAG не задавал, строка — задал, `None` — задал что-то с Jinja
или кавычками), макрос пишет info «`<ключ>` в DAG-conf=… переопределяется OL-значением=…»
и возвращает своё значение. `prefix` для скаляров отбрасывается — дописывать текст DAG'а
слева было бы прямым нарушением правила «OL побеждает».

Поведение по веткам (с журналированием через `ol_macro(field, forced, dag_cur)`, где `dag_cur` —
DAG-значение ключа, переданное парсом):

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
| `None` или `True` | `True` | dict | да, всё годно | валиден, probe `True` | **вкл** | info из `_emit` («мердж» / «подмешан» / «дописан») |
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

**Сброс модульного состояния в `reset_state()`**: `_warned.clear()`, `_cfg.cache_clear()`,
`_validate_cfg.cache_clear()`, `_jar_memo.clear()` **и `_passthrough_cache = None`**.
Последнее — исправление найденного бага: `passthrough_exceptions()` кэширует результат
на модуле, а тесты подменяют `airflow.*` в `sys.modules`; без сброса кэш переживает границу
теста и даёт порядко-зависимые падения `test_passthrough_*` (воспроизведено 2026-07-31:
7 падений в полном прогоне, зелено поодиночке).

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
- **Инвариант 16 — `spark.extraListeners` мерджится.** DAG-CSV никогда не затирается: он
  либо передан макросу литералом и слит через `merge_listeners` (DAG-listener'ы первыми,
  OL-listener последним, дедуп), либо стоит текстом слева от вызова макроса. Пишется
  info-лог с результатом.
- **Инвариант 14 — jar мерджится, итог пишется в атрибут `jars`.** Политика не затирает ни
  `operator.jars`, ни `conf["spark.jars"]`: оба канала склеиваются на парсе через
  `merge_jars` и попадают в атрибут `jars`. Запись итога в `conf["spark.jars"]` **запрещена** —
  при заданном DAG'ом `jars=` Spark этот ключ игнорирует (§2).
- **Инвариант 17 — разделитель принадлежит макросу.** Ни одна собранная на парсе строка не
  содержит запятой, соседствующей с вызовом макроса. Пустой результат макроса обязан давать
  корректное значение conf, а не `"a.jar,"`.
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
Ветка `listener` — по трём каналам `dag_cur`:

- **`dag_cur=""`** (DAG ключ не задавал), Variable `spark.extraListeners = "com.example.X"`
  → `"com.example.X"` + info «подмешан». Разделителя нет.
- **`dag_cur="com.example.A,com.example.B"`** (безопасный литерал), Variable
  `spark.extraListeners = "io.openlineage..."` → `"com.example.A,com.example.B,io.openlineage..."`
  + info «мердж: …». Инвариант 16.
- **`dag_cur="com.example.A,com.example.B"`**, Variable `spark.extraListeners = "com.example.A"`
  → `"com.example.A,com.example.B"` — дедуп, класс не дублируется. Защита от двух инстансов
  одного листенера.
- **`dag_cur=None`** (DAG-значение со своей Jinja осталось слева) → `",io.openlineage..."`
  — с ведущей запятой + info «дедуп невозможен».
- **Variable без `spark.extraListeners`** → `""` + warning «listener не подмешан», при любом
  канале.
- **`_dag_channel` (парс)**: `""`/`None`/пробелы → `("", "")`; `"a.jar,b.jar"` → `("", "a.jar,b.jar")`;
  `"{{ params.x }}"` → `("{{ params.x }}", None)`; `"it's.jar"` → `("it's.jar", None)`
  (кавычка сломала бы литерал в тексте вызова макроса).
- **`ol_macro('url')` при Variable с `spark.openlineage.transport.url = "   "` (пробелы) → `""` + warning.**
- **`ol_macro('url')` при Variable с `spark.openlineage.transport.url = 5000` (число) → `""` + warning.**
- **`ol_macro('url')` при Variable с `spark.openlineage.transport.url = "marquez:5000"` (без схемы) → `""` + warning.**
- **`ol_macro('namespace')` при Variable с `spark.openlineage.namespace = "   "` → `""` + warning.**
- **`ol_macro('url', none, 'http://dag-marquez:5000')` при Variable с
  `spark.openlineage.transport.url = "http://marquez:5000"` → возвращает значение из Variable
  + info-лог с обоими значениями.** Инвариант 13: OL побеждает, DAG-значение только в логе.
- **Тексты причин попарно различимы.** Тест прогоняет сценарии отказа (нет Variable, битый
  JSON, не объект, негодная форма, неполная Variable, дедлайн зонда, нет эндпоинтов, все
  standby, эндпоинты недоступны) и сравнивает **фактические записи `caplog`** на попарную
  различимость. Констант `_MSG_*` нет — сравнивается вывод, а не список литералов.
- **`ol_macro('jar', none, 'a.jar')`** при валидном `openlineage_jar` и probe `True` →
  `"a.jar,<openlineage-jar>"`. Инвариант 14.
- **`ol_macro('jar', none, '')`** при probe `True` → `"<openlineage-jar>"`, без разделителя.
- **`ol_macro('jar', none, none)`** при probe `True` → `",<openlineage-jar>"`, с ведущей запятой.
- **`ol_macro('jar')` при Variable с `openlineage_jar = ""` → `""` + warning «не задано».**
- **`ol_macro('jar')` при Variable с `openlineage_jar = "/opt/x.jar"` (без схемы) → `""` + warning «без схемы».**
- **`ol_macro('jar')` при Variable с `openlineage_jar` валидным и probe `False` → `""` + warning «не подтверждён в HDFS».**
- **`ol_macro('jar')` кеширует результат probe по URI** — второй вызов с тем же URI не идёт в сеть
  (мемо `_jar_memo`).
- **`ol_macro` при Variable валидной пишет info-лог на каждом вызове.** Инвариант 15: ни одна
  ветка не молчит.
- **`inject_openlineage` пишет в `conf` и в атрибут `jars` — только строки с вызовом макроса.**
  Проверка на обеих раскладках атрибутов (4.1.1 и 4.10.0): после инъекции
  `conf["spark.openlineage.transport.url"]` содержит `{{ __openlineage_v1('url'`, а атрибут
  `jars` заканчивается вызовом макроса. Ни одно значение из Variable в conf на парсе не
  попадает.
- **`inject_openlineage` НЕ читает Variable.** `Variable.get` подменён на `pytest.fail`;
  полный прогон по обеим раскладкам его не трогает.
- **`inject_openlineage` НЕ ходит в сеть.** `urlopen` и `jar_available` подменены на
  `pytest.fail`. Probe зовётся только из `_resolve_jar` на рендере.
- **`inject_openlineage` НЕ читает `OPENLINEAGE_JAR` env.** Ключ удалён из `os.environ`,
  инъекция проходит полностью.
- **Инвариант 17: никаких висячих запятых.** DAG задал `jars="a.jar"`, Variable выключена
  (`enabled: false`) → отрендеренный атрибут `jars` равен ровно `"a.jar"`, без хвостовой
  запятой. То же для `spark.extraListeners`.
- **Инвариант 14: итог jar-мерджа не уезжает в `conf["spark.jars"]`.** DAG задал
  `conf["spark.jars"]="a.jar"` → после рендера `a.jar` присутствует в атрибуте `jars`.
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
| `airflow/config/ol_policy/__init__.py` | `inject_openlineage` регистрирует макрос **и** пишет строки в `conf` + атрибут `jars`, не читая Variable/HDFS; уходят чтение `OPENLINEAGE_JAR`, `jar_path`/`jar_available` на парсе, `ol_conf_template`, `foreign_listener`, `_OUR_LISTENERS`, константа `LISTENER`; новые `_dag_channel`, `_macro_call` (парс) и `_emit` (рендер); `_validate_cfg()` — zero-arg под `lru_cache(maxsize=1)`, агрегирует недостающие поля в один warning на процесс; `_resolve_jar(cfg, dag_cur)` зовёт probe на рендере; `ol_macro(field, forced, dag_cur)` на четыре `field`; `reset_state` дополнительно сбрасывает `_passthrough_cache`; тексты сообщений остаются inline |
| `airflow/config/ol_policy/logger.py` | без изменений (механизм `warn_once` остаётся для других мест, если они появятся) |
| `airflow/config/ol_policy/utils.py` | новая функция `merge_listeners(dag_cur, our_listener)`: сплит по запятой (с Jinja-сохранением, как в `_jar_items`), дедуп, порядок: DAG-listener'ы первыми, наш последним; `merge_jars` без изменений |
| `airflow/scripts/start-airflow.sh` | сборка JSON упрощается: `url`/`namespace`/`openlineage_jar` берутся как литералы (больше не из ENV); `OPENLINEAGE_*` ENV-переменные удалены из heredoc |
| `env_example` | строки `OPENLINEAGE_URL`, `OPENLINEAGE_NAMESPACE`, `OPENLINEAGE_JAR` удалены; комментарий к `OPENLINEAGE_CONFIG_RESEED` уточнён |
| `docker-compose.yml` | ключи `environment.airflow.OPENLINEAGE_URL/NAMESPACE/JAR` удалены; `OPENLINEAGE_CONFIG_RESEED=false` остаётся |
| `airflow/config/tests/test_ol_policy.py` | новые тесты §9; удалены тесты `OPENLINEAGE_JAR env`-зависимости (фикстура `jar_env` остаётся только для тех, которые проверяют отсутствие чтения env); таблица истинности §4.5 — все строки; тесты на `_validate_cfg` (один warning, один вызов на процесс); тесты на `_render_jar_merge` (мердж с `a.jar`); тесты на инвариант 13 (OL побеждает DAG-conf + info-лог) |
| `airflow/config/tests/conftest.py` | без изменений; фикстура `_reset_policy_state` зовёт `reset_state()`, который теперь сбрасывает ещё `_validate_cfg` и `_passthrough_cache` |
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
- **Дедуп невозможен, когда DAG-значение содержит свою Jinja.** Канал `dag_cur=None`
  отдаёт значение с ведущей запятой, не зная, что уже стоит слева. DAG, который положил
  `{{ … }}` в `spark.extraListeners` и получил из него OL-класс, получит его дважды —
  два инстанса листенера и дублирующиеся события. Парс пишет warning; на стенде
  DAG-listener'ы задаются литералами, поэтому канал `None` практически не задействован.
- **Кавычка в DAG-значении отправляет его в канал `None`.** `_dag_channel` считает
  небезопасными `{{`, `{%`, `'`, `"` — литерал вставляется в текст вызова макроса в
  одинарных кавычках, и кавычка внутри сломала бы шаблон. Цена — потеря дедупа, не поломка.
- **Порядок рендера `conf` и `jars` не важен.** Оба — независимые `template_fields`, и ни
  один макрос не читает результат другого: всё, что нужно, передано аргументами на парсе.
  Это прямое следствие отказа от `_resolve_local`/`pass_context`.
- **`spark.extraListeners` и пробелы / формат.** Spark сплитит по запятой через `Utils.stringToSeq`
  (грounding §2), фильтрует пустые и пробельные. `merge_listeners` тоже фильтрует и сплитит
  по запятой, но **сохраняет пробелы внутри токенов** — на стенде классы без пробелов,
  риск нулевой. Если понадобится — добавить `strip()` к каждому токену в `merge_listeners`.
- **Цикл 2 из §10 предка** (вынос `enabled` в DAG-уровневый override с per-DAG namespace) —
  отдельный дизайн.

## 12. Журнал ревизий

### Ревизия 3 (cycle 1 — эта, 2026-07-31)

Ревизия 2 была реализована на 8 коммитов и **откачена** (`git reset --hard 243389b`, коммиты
сохранены на ветке `backup/ol-cycle1-2026-07-31`). Причина — два дефекта самой спеки,
обнаруженные проверкой фактов на живой Airflow 2.6.3.

| Было (Ревизия 2) | Стало (Ревизия 3) | Почему |
| --- | --- | --- |
| `_resolve_local` читает текущее DAG-значение через `airflow.plugins_manager.get_template_locals` | **`_resolve_local` удалена.** DAG-значение передаёт парс — параметром `dag_cur` тремя каналами (`""` / литерал / `None`) | API не существует: `hasattr(airflow.plugins_manager, "get_template_locals") is False` на 2.6.3. В проде функция всегда возвращала бы `None`: мердж listener'ов выродился бы в OL-only, `spark.jars` DAG'а был бы затёрт. Тесты не ловили — монкипатчили саму функцию |
| `inject_openlineage` регистрирует **только** макрос, `conf`/`jars` не трогает | `inject_openlineage` регистрирует макрос **и** пишет строки в `conf` и атрибут `jars` | иначе макросу неоткуда взяться: лайнидж работал бы только у DAG'ов, вписавших `{{ __openlineage_v1(…) }}` руками — против цели «без правок в DAG'ах» |
| Итог `merge_jars` пишется в `conf["spark.jars"]`, атрибут `jars` не трогается | Итог пишется в **атрибут `jars`** | `Option(jars).orElse(sparkProperties.get(JARS.key))`: при заданном DAG'ом `jars=` Spark `spark.jars` не смотрит — наш jar не приехал бы |
| «OL побеждает через порядок рендера Jinja» | OL побеждает **порядком ключей на парсе**: наши ключи пишутся после `**cur_conf` | порядок рендера ничего не решает: если DAG положил литерал, рендерить нечего |
| `_validate_cfg(cfg: dict)` под `lru_cache(maxsize=1)` | `_validate_cfg()` без аргументов, сама зовёт `_cfg()` | `lru_cache` хэширует аргументы; dict нехэшируем — `TypeError` на первом вызове |
| Тексты предупреждений вынесены в константы `_MSG_*` | Тексты **inline**; различимость проверяется по фактическому `caplog` | константы существовали только ради тестов; дедуп `warn_once` опирается на ключ-кортеж, не на текст |
| `reset_state()` сбрасывает `_warned`, `_cfg`, `_jar_memo` | плюс `_validate_cfg` и **`_passthrough_cache`** | без сброса `_passthrough_cache` sys.modules-заглушки тестов дают 7 порядко-зависимых падений `test_passthrough_*` |
| — | Новый **инвариант 17**: разделитель принадлежит макросу | сборка на парсе иначе даёт `"a.jar,"` при выключенном lineage |

### Ревизия 2 (cycle 1 — предыдущая)

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
