# ol_policy Comment Diet Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Привести комментарии и docstring'и пакета `airflow/config/ol_policy` к правилу «код читается сам»: module-docstring в одну строку, docstring функции — что делает плюс reST-поля, инлайн-комментарии только там, где код без них выглядит ошибкой; пять переименований вместо комментариев.

**Architecture:** Правка чисто текстовая плюс переименование пяти символов. Ни одна сигнатура, ни один тип, ни одна ветка поведения не меняются. Задачи нарезаны по модулям так, чтобы каждая заканчивалась зелёным прогоном всего набора; задача с переименованиями, которые видны снаружи пакета, идёт отдельно и правит тесты и смоук-скрипт в том же шаге.

**Tech Stack:** Python 3.10, Apache Airflow 2.6.3, провайдер `apache-airflow-providers-apache-spark` 4.1.1, pytest. Внутри пакета только стандартная библиотека.

## Global Constraints

- Спека: `docs/superpowers/specs/2026-08-02-ol-policy-comment-diet-design.md`. Все тексты docstring'ов ниже взяты из неё.
- Комментарии, docstring'и и сообщения логов — на русском; идентификаторы, ключи conf и имена ENV — английские.
- Docstring есть у каждой функции и метода. Формат PyCharm reST: строка summary, затем `:param <имя>:`, `:return:`, `:raises <ExcType>:` там, где функция бросает. Поля `:type:` / `:rtype:` не пишутся — типы на аннотациях.
- Аннотации типов полные, включая `-> None`. `typing.Any` запрещён.
- Длина строки — не больше 120 символов.
- Ни один модуль пакета не импортирует Airflow на уровне модуля: импорт только внутри функций.
- Тексты warning'ов и ключи дедупликации не меняются: их попарную различимость проверяет `test_failure_reasons_are_pairwise_distinct`.
- Поведение не меняется ни в одной ветке. Тесты по смыслу не правятся — только имена символов, к которым они обращаются.
- Прогон после каждой задачи: `python -m pytest airflow/config/tests -q` из корня `hadoop_cluster`. Эталон — `203 passed` до и после всей работы.
- Цикл рефакторинга вместо RED→GREEN: набор зелёный до правки, правится вместе с кодом в том же шаге, зелёный после. Красной фазы здесь нет — новых требований к поведению не появляется.

## File Structure

| Файл | Ответственность | Задачи |
|---|---|---|
| `airflow/config/ol_policy/probe.py` | зонд jar в HDFS | 1 |
| `airflow/config/ol_policy/variable.py` | чтение и проверка Variable | 2 |
| `airflow/config/ol_policy/callback.py` | колбэк-фаза, запись conf/jars | 2 |
| `airflow/config/tests/test_ol_policy.py` | набор тестов политики | 2 |
| `tests/test-airflow.bat` | смоук стенда | 2 |
| `airflow/config/ol_policy/operator.py` | раскладки оператора, тумблер | 3 |
| `airflow/config/ol_policy/logger.py` | дедуплицированные warning'и | 3 |
| `airflow/config/ol_policy/utils.py` | хелперы: id таски, мердж CSV | 3 |
| `airflow/config/ol_policy/parse.py` | парс-фаза | 3 |
| `airflow/config/ol_policy/__init__.py` | точка входа и реэкспорты | 3 |
| `airflow/config/ol_policy/handlers.py` | исключения политики | 3 |
| `airflow/config/ol_policy/hadoop_conf.py` | разбор конфигов Hadoop | 4 |
| `CHANGELOG.md` | история изменений | 5 |
| `docs/superpowers/specs/2026-08-02-ol-policy-cache-removal-design.md` | спека прошлой задачи | 5 |
| `docs/superpowers/plans/2026-08-02-ol-policy-cache-removal.md` | план прошлой задачи | 5 |

---

### Task 1: `probe.py` — docstring'и, инлайны, `_EndpointOutcome`

**Files:**
- Modify: `airflow/config/ol_policy/probe.py`

**Interfaces:**
- Consumes: `hadoop_conf.resolve_webhdfs_urls`, `handlers.NoEndpointsError`, `logger.warn_once`.
- Produces: неизменные публичные имена `jar_path(jar_uri: str) -> str | None`, `jar_available(jar_uri: str, path: str) -> bool`, `ENDPOINT_TIMEOUT_SEC`, `_PROBE_DEADLINE_SEC`, `_RETRY_PAUSE_SEC`, `resolve_webhdfs_urls`, `_sleep`, `_probe`, `_query_endpoint`. Внутренний алиас `_Outcome` переименован в `_EndpointOutcome`; `_ProbeOutcome` имя сохраняет.

- [ ] **Step 1: Зафиксировать зелёный старт**

Run: `python -m pytest airflow/config/tests -q`
Expected: `203 passed`.

- [ ] **Step 2: Заменить module-docstring на одну строку**

Заменить весь текущий блок (строки 1–7, от `"""Зонд openlineage-spark jar` до закрывающих кавычек) на:

```python
"""Зонд наличия openlineage-spark jar в HDFS через WebHDFS."""
```

- [ ] **Step 3: Переименовать `_Outcome` и снять комментарии над алиасами**

Было:

```python
# Исход опроса одного эндпоинта WebHDFS.
_Outcome = Literal["found", "absent", "standby", "error"]

# Исход целого зонда: down — кластер не дал авторитетного ответа ни на одном проходе.
_ProbeOutcome = Literal["found", "absent", "down"]
```

Стало:

```python
_EndpointOutcome = Literal["found", "absent", "standby", "error"]
_ProbeOutcome = Literal["found", "absent", "down"]
```

Обновить обе аннотации, где встречается `_Outcome`: возвращаемый тип `_query_with_auth` и возвращаемый тип `_query_endpoint`. Проверить, что других вхождений нет: `grep -n "_Outcome" airflow/config/ol_policy/probe.py` должен показать только `_EndpointOutcome` и `_ProbeOutcome`.

- [ ] **Step 4: Снять блок арифметики бюджета над константами**

Удалить весь комментарий от `# Ограничители зонда:` до `# jar_available: поток обрежется по нему, чем бы он ни был занят.` включительно. Константы остаются как есть:

```python
_PROBE_DEADLINE_SEC = 17.0
ENDPOINT_TIMEOUT_SEC = 2.0
_RETRY_PAUSE_SEC = 0.5

# Реэкспорт ради monkeypatch: тесты подменяют символ, который читает этот модуль.
resolve_webhdfs_urls = hadoop_conf.resolve_webhdfs_urls
_sleep = time.sleep
```

Арифметику бюджета удерживает ассерт в `test_probe_deadline_covers_two_passes_over_ha_pair` — комментарий её не сторожит.

- [ ] **Step 5: Ужать docstring'и функций**

`jar_path`:

```python
def jar_path(jar_uri: str) -> str | None:
    """Возвращает абсолютный путь внутри HDFS из значения поля ``openlineage_jar``.

    :param jar_uri: значение поля ``openlineage_jar``.
    :return: абсолютный путь для WebHDFS либо None, если значение без схемы или без пути.
    """
```

`_spnego_header`:

```python
def _spnego_header(endpoint: str) -> str | None:
    """Строит SPNEGO-заголовок Authorization для эндпоинта.

    :param endpoint: адрес вида ``http://host:port``.
    :return: строка ``Negotiate <base64>`` либо None, если токен получить не удалось.
    """
```

`_probe`:

```python
def _probe(path: str) -> _ProbeOutcome:
    """Опрашивает эндпоинты WebHDFS, при сплошных неавторитетных ответах — второй проход.

    :param path: абсолютный путь jar'а в HDFS.
    :return: "found", "absent" либо "down" — ни один эндпоинт не ответил авторитетно.
    :raises handlers.NoEndpointsError: резолвер не дал ни одного эндпоинта.
    """
```

`jar_available`:

```python
def jar_available(jar_uri: str, path: str) -> bool:
    """Проверяет наличие openlineage-spark jar в HDFS, прерываясь по дедлайну.

    :param jar_uri: значение поля ``openlineage_jar`` — для текстов warning'ов.
    :param path: разобранный путь jar'а для WebHDFS.
    :return: True, если jar доступен; False во всех остальных исходах.
    """
```

Docstring'и `_is_standby`, `_query_with_auth`, `_query_endpoint`, `_probe_worker` уже состоят из summary и полей — их не трогать.

- [ ] **Step 6: Прогнать набор**

Run: `python -m pytest airflow/config/tests -q`
Expected: `203 passed`.

- [ ] **Step 7: Коммит**

```bash
git add airflow/config/ol_policy/probe.py
git commit -m "refactor(ol_policy): trim probe docstrings and comments, rename _Outcome to _EndpointOutcome"
```

---

### Task 2: `variable.py` + `callback.py` — переименования, видимые снаружи пакета

**Files:**
- Modify: `airflow/config/ol_policy/variable.py`
- Modify: `airflow/config/ol_policy/callback.py`
- Modify: `airflow/config/tests/test_ol_policy.py`
- Modify: `tests/test-airflow.bat`

**Interfaces:**
- Consumes: `logger.warn_once`, `operator.operator_attrs`, `operator.lineage_forced`, `operator._spark_submit_operator`, `probe.jar_path`, `probe.jar_available`, `utils.merge_csv`, `utils.dag_and_task_ids`.
- Produces: `variable.read_config() -> dict[str, object] | None` (бывший `_cfg`), `variable.validate_config(cfg: dict[str, object] | None) -> Config | None` (бывший `_validate`), `callback._write_lineage(task: object, attrs: operator.OperatorAttrs, config: variable.Config) -> None` (бывший `_write`). `variable.Config`, `variable.VARIABLE`, `variable._clean`, `callback.ol_execute_callback`, `callback._inject` имён не меняют.

- [ ] **Step 1: Переименовать функции в `variable.py`**

`_cfg` → `read_config`, `_validate` → `validate_config`. Module-docstring и docstring'и — так:

```python
"""Чтение и проверка Airflow Variable ``openlineage_config``."""
```

```python
def read_config() -> dict[str, object] | None:
    """Читает Variable ``openlineage_config`` и разбирает её JSON.

    :return: конфиг с ключами enabled, spark_conf, openlineage_jar; None, если прочитать
        не удалось или форма неверна — причина записана в лог.
    """
```

```python
def validate_config(cfg: dict[str, object] | None) -> Config | None:
    """Проверяет годность разобранного конфига, сообщая о недостающих полях одним warning'ом.

    :param cfg: конфиг, разобранный ``read_config``, либо None.
    :return: проверенный конфиг либо None, если он непригоден для включения лайниджа.
    """
```

`_clean` теряет ничего — его docstring уже минимален; проверить, что в нём нет обоснований.

Снять комментарий `# Форма проверяется здесь, содержимое полей — в _validate: тут решается, / # тот ли это документ вообще, там — годится ли он для включения лайниджа.` целиком.

Docstring класса `Config` оставить как есть — одна строка.

- [ ] **Step 2: Обновить `callback.py` под новые имена и ужать тексты**

Module-docstring:

```python
"""Колбэк-фаза: резолв значений лайниджа и запись conf/jars на воркере до ``execute()``."""
```

В `_inject`: вызовы `variable._cfg()` → `variable.read_config()`, `variable._validate(cfg)` → `variable.validate_config(cfg)`; вызов `_write(task, attrs, config)` → `_write_lineage(task, attrs, config)`; удалить комментарий `# причина уже названа парс-фазой`. Docstring:

```python
def _inject(task: object) -> None:
    """Проверяет гейты лайниджа и пишет его значения в таску.

    :param task: execution-копия оператора из контекста.
    :return: None.
    """
```

`_write` переименовать в `_write_lineage`; из docstring'а уходят оба абзаца-обоснования, инвариант порядка остаётся одной строкой у самого кода:

```python
def _write_lineage(task: object, attrs: operator.OperatorAttrs, config: variable.Config) -> None:
    """Пишет значения лайниджа в атрибуты jars и conf таски.

    :param task: execution-копия оператора.
    :param attrs: имена атрибутов conf/jars текущей раскладки.
    :param config: проверенный конфиг из Variable.
    :return: None.
    """
```

Перед первым `setattr` оставить ровно одну строку комментария:

```python
    # jars пишется раньше conf: обрыв между setattr'ами оставит лишний jar, но не листенер без jar'а.
```

Docstring `ol_execute_callback` оставить как есть — он уже summary плюс поля.

- [ ] **Step 3: Обновить обращения в тестах**

В `airflow/config/tests/test_ol_policy.py` заменить все вхождения:

- `ol_policy.variable._cfg` → `ol_policy.variable.read_config`
- `ol_policy.variable._validate` → `ol_policy.variable.validate_config`

Проверить полный список вхождений до правки, не усекая вывод:

Run: `grep -n "variable\._cfg\|variable\._validate" airflow/config/tests/test_ol_policy.py`
Expected: около восьми строк — в `test_cfg_returns_dict`, `test_cfg_rejects_empty_object`, `test_cfg_rejects_old_shape`, `test_cfg_returns_none_and_warns`, `test_cfg_warns_about_auth`, `test_validate_cfg_aggregates_missing_fields`, `test_seeded_value_is_accepted_by_the_policy`, `test_callback_never_raises`. Каждое заменить; после правки та же команда не должна найти ничего.

Имена самих тестов не менять — они описывают поведение, а не символ.

- [ ] **Step 4: Обновить смоук**

В `tests/test-airflow.bat`, шаг 13, заменить `ol_policy.variable._validate(ol_policy.variable._cfg())` на `ol_policy.variable.validate_config(ol_policy.variable.read_config())`. Остальную строку не трогать.

- [ ] **Step 5: Проверить, что старых имён не осталось**

Run: `grep -rn "variable\._cfg\|variable\._validate\|callback\._write\b" --include="*.py" --include="*.bat" airflow tests`
Expected: пусто.

- [ ] **Step 6: Прогнать набор**

Run: `python -m pytest airflow/config/tests -q`
Expected: `203 passed`.

- [ ] **Step 7: Коммит**

```bash
git add airflow/config/ol_policy/variable.py airflow/config/ol_policy/callback.py airflow/config/tests/test_ol_policy.py tests/test-airflow.bat
git commit -m "refactor(ol_policy): rename _cfg/_validate/_write to speaking names, trim their docstrings"
```

---

### Task 3: `operator.py`, `logger.py`, `utils.py`, `parse.py`, `__init__.py`, `handlers.py`

**Files:**
- Modify: `airflow/config/ol_policy/operator.py`
- Modify: `airflow/config/ol_policy/logger.py`
- Modify: `airflow/config/ol_policy/utils.py`
- Modify: `airflow/config/ol_policy/parse.py`
- Modify: `airflow/config/ol_policy/__init__.py`
- Modify: `airflow/config/ol_policy/handlers.py`

**Interfaces:**
- Consumes: ничего нового.
- Produces: имена не меняются — `apply_policy`, `reset_state`, `merge_csv`, `task_dag`, `dag_and_task_ids`, `warn_once`, `reset`, `passthrough_exceptions`, `operator_attrs`, `lineage_forced`, `OperatorAttrs`, `inject_openlineage`, `NoEndpointsError`.

- [ ] **Step 1: `operator.py`**

Module-docstring:

```python
"""Совместимость с раскладками ``SparkSubmitOperator`` и тумблер лайниджа из ``params``."""
```

Комментарий над `_PASSTHROUGH_NAMES` сократить до одной строки — именно он объясняет неочевидное:

```python
# Импорт ленивый: этот модуль подключается раньше, чем airflow.settings завершает инициализацию.
_PASSTHROUGH_NAMES = ("AirflowTaskTimeout", "AirflowClusterPolicyViolation", "AirflowClusterPolicySkipDag")
```

Docstring'и:

```python
def passthrough_exceptions() -> tuple[type[BaseException], ...]:
    """Собирает классы исключений Airflow, которые политика пропускает наружу.

    :return: кортеж классов; пустой, если Airflow недоступен.
    """
```

```python
def operator_attrs(task: object) -> OperatorAttrs | None:
    """Определяет имена атрибутов conf и jars у этого оператора.

    :param task: таска Airflow.
    :return: имена атрибутов либо None, если раскладка незнакома.
    """
```

```python
def _level_forced(owner: object, level: str, dag_id: str, task_id: str) -> bool | None:
    """Читает тумблер ``params['openlineage']`` одного уровня лесенки.

    :param owner: таска либо DAG, чьи ``params`` читаются.
    :param level: имя уровня для сообщения ("таски" либо "DAG'а").
    :param dag_id: идентификатор DAG'а для ключа дедупликации.
    :param task_id: идентификатор таски для ключа дедупликации.
    :return: True, False либо None, если уровень не высказался или значение негодно.
    """
```

```python
def lineage_forced(task: object) -> bool | None:
    """Читает форс лайниджа из DAG'а: сначала ``task.params``, затем ``dag.params``.

    :param task: таска Airflow.
    :return: True — форс-включение, False — форс-выключение, None — решает Variable.
    """
```

Docstring'и `_spark_submit_operator`, `_looks_like_spark_submit`, `OperatorAttrs` уже минимальны — оставить.

- [ ] **Step 2: `logger.py`**

Module-docstring:

```python
"""Логгер политики с дедупликацией сообщений."""
```

```python
def warn_once(key: tuple[str, ...], msg: str, *args: object, exc_info: bool = False) -> None:
    """Пишет warning один раз на процесс для каждого ключа дедупликации.

    :param key: ключ дедупликации.
    :param msg: шаблон сообщения для logging.
    :param args: аргументы шаблона; значения из Variable сюда не передаются.
    :param exc_info: писать ли traceback текущего исключения.
    :return: None.
    """
```

```python
def reset() -> None:
    """Сбрасывает дедупликацию warning'ов.

    :return: None.
    """
```

- [ ] **Step 3: `utils.py`**

Module-docstring:

```python
"""Общие хелперы политики: идентификаторы таски, мердж CSV-значений."""
```

```python
def task_dag(task: object) -> object | None:
    """Возвращает DAG таски, не бросая на таске без DAG'а.

    :param task: таска Airflow.
    :return: объект DAG'а либо None.
    """
```

```python
def _csv_items(value: object) -> list[str]:
    """Разбирает CSV-значение одного источника в список элементов.

    Значение с Jinja возвращается целиком: разбиение по запятой порвало бы выражение.

    :param value: значение атрибута оператора либо ключа conf.
    :return: список непустых элементов; для не-строки — пустой список.
    """
```

```python
def merge_csv(*sources: object) -> str:
    """Склеивает CSV-источники в порядке перечисления, убирая дубликаты.

    :param sources: значения источников: атрибут ``jars``, ключи conf, наше значение.
    :return: элементы через запятую; "" если все источники пусты.
    """
```

Одна фраза про Jinja в `_csv_items` остаётся: без неё ветка `if "{{" in value` читается как случайность. Docstring `dag_and_task_ids` уже минимален.

- [ ] **Step 4: `parse.py`**

Module-docstring:

```python
"""Парс-фаза: дозапись колбэка лайниджа в ``on_execute_callback`` таски."""
```

```python
def inject_openlineage(task: object) -> None:
    """Идемпотентно дописывает колбэк лайниджа в ``on_execute_callback`` Spark-таски.

    :param task: экземпляр ``SparkSubmitOperator``; мутируется на месте.
    :return: None.
    """
```

- [ ] **Step 5: `__init__.py`**

Module-docstring:

```python
"""Точка входа cluster policy: инъекция OpenLineage в Spark-таски Airflow."""
```

Снять комментарий над блоком импортов подмодулей (`# Подмодули импортируются целиком ради путей…`) и комментарий над реэкспортом `merge_csv` (`# Реэкспорт утилит: тесты и вызывающий код…`). Импорты и реэкспорт остаются как есть.

```python
def apply_policy(task: object) -> None:
    """Точка входа cluster policy: пропускает Spark-таски к инъекции, гася свои ошибки.

    :param task: любая таска Airflow; мутируется на месте на этапе парсинга.
    :return: None.
    """
```

```python
def reset_state() -> None:
    """Сбрасывает модульное состояние политики.

    :return: None.
    """
```

- [ ] **Step 6: `handlers.py`**

Module-docstring отсутствует — добавить, класс не трогать:

```python
"""Исключения политики."""


class NoEndpointsError(RuntimeError):
    """Резолвер не дал ни одного WebHDFS-эндпоинта."""
```

- [ ] **Step 7: Прогнать набор**

Run: `python -m pytest airflow/config/tests -q`
Expected: `203 passed`.

- [ ] **Step 8: Коммит**

```bash
git add airflow/config/ol_policy/operator.py airflow/config/ol_policy/logger.py airflow/config/ol_policy/utils.py airflow/config/ol_policy/parse.py airflow/config/ol_policy/__init__.py airflow/config/ol_policy/handlers.py
git commit -m "refactor(ol_policy): trim module and function docstrings across the remaining modules"
```

---

### Task 4: `hadoop_conf.py` — docstring'и и `_expand_vars`

**Files:**
- Modify: `airflow/config/ol_policy/hadoop_conf.py`

**Interfaces:**
- Consumes: ничего нового.
- Produces: `hadoop_conf_dir() -> str`, `parse_hadoop_xml(filename: str) -> dict[str, str]`, `resolve_webhdfs_urls() -> list[str]` — имена и сигнатуры прежние. Внутренняя `_expand` переименована в `_expand_vars`.

- [ ] **Step 1: Module-docstring в одну строку**

Весь текущий блок (строки 1–11) заменить на:

```python
"""Разбор конфигов Hadoop и резолв эндпоинтов WebHDFS."""
```

- [ ] **Step 2: Переименовать `_expand` и ужать её docstring**

```python
def _expand_vars(value: str, props: dict[str, str]) -> str:
    """Раскрывает ссылки ``${name}`` по другим свойствам того же файла.

    Неизвестная переменная и цикл оставляют плейсхолдер, а не роняют разбор.

    :param value: сырое значение свойства.
    :param props: все свойства файла для подстановки.
    :return: значение с раскрытыми ссылками.
    """
```

Единственное вхождение вызова — в `parse_hadoop_xml`, в финальном словарном включении: `{name: _expand(value, props) ...}` → `{name: _expand_vars(value, props) ...}`.

Комментарий над `_MAX_DEPTH` остаётся — он объясняет, зачем ограничение вообще есть:

```python
# Ограничение глубины раскрытия: цикл `a=${b}; b=${a}` обязан завершиться.
_MAX_DEPTH = 20
```

- [ ] **Step 3: Ужать остальные docstring'и**

```python
def parse_hadoop_xml(filename: str) -> dict[str, str]:
    """Разбирает ``*-site.xml`` из каталога конфигов в словарь свойств.

    :param filename: имя файла в ``hadoop_conf_dir()``.
    :return: свойства файла с раскрытыми ``${var}``; свойства без имени или значения пропущены.
    :raises OSError: файл недоступен.
    :raises ElementTree.ParseError: файл не является корректным XML.
    """
```

```python
def resolve_webhdfs_urls() -> list[str]:
    """Определяет эндпоинты WebHDFS по конфигам кластера: HA-список, одиночный адрес либо фолбэк.

    :return: список адресов вида ``http://host:port`` без завершающего слэша;
        пустой список, если по конфигам эндпоинты определить нельзя.
    :raises OSError: конфиг кластера недоступен.
    :raises ElementTree.ParseError: конфиг кластера не является корректным XML.
    """
```

Комментарий `# Фолбэк стенда: http-address не задан, адрес берётся из fs.defaultFS.` оставить: без него последняя ветка читается как дубль предыдущей. Комментарий `# noqa: S314 конфиги кластера, не ввод` оставить — он объясняет подавление правила линтера. Docstring `hadoop_conf_dir` уже минимален.

- [ ] **Step 4: Прогнать набор**

Run: `python -m pytest airflow/config/tests -q`
Expected: `203 passed` — `test_hadoop_conf.py` обращается только к публичным именам, правка их не касается.

- [ ] **Step 5: Коммит**

```bash
git add airflow/config/ol_policy/hadoop_conf.py
git commit -m "refactor(ol_policy): trim hadoop_conf docstrings, rename _expand to _expand_vars"
```

---

### Task 5: Синхронизация документации

**Files:**
- Modify: `CHANGELOG.md`
- Modify: `docs/superpowers/specs/2026-08-02-ol-policy-cache-removal-design.md`
- Modify: `docs/superpowers/plans/2026-08-02-ol-policy-cache-removal.md`

**Interfaces:**
- Consumes: имена, зафиксированные задачами 2 и 4.
- Produces: документация, не ссылающаяся на несуществующие символы.

- [ ] **Step 1: Найти все упоминания переименованных символов в документации**

Run: `grep -rn "variable\._cfg\|variable\._validate\|_validate_cfg\|callback\._write\b\|_expand\b" --include="*.md" . | grep -v node_modules`
Expected: вхождения в `CHANGELOG.md` и в двух документах прошлой задачи (`2026-08-02-ol-policy-cache-removal-*`). Записи более ранних дат (июльские спеки и планы) — исторические, их не трогать: они описывают состояние кода на свою дату.

- [ ] **Step 2: Обновить спеку и план прошлой задачи**

В `docs/superpowers/specs/2026-08-02-ol-policy-cache-removal-design.md` и `docs/superpowers/plans/2026-08-02-ol-policy-cache-removal.md` заменить в описаниях итогового состояния `_cfg` на `read_config` и `_validate` на `validate_config`. Упоминания удалённых `_validate_cfg`, `_cfg_with_stamp`, `_load_cfg` оставить как есть — это имена, которых уже нет, и текст описывает именно их удаление.

- [ ] **Step 3: Добавить запись в CHANGELOG**

В раздел `## [Unreleased]` → `### Изменено` дописать:

```markdown
- ol_policy: комментарии и docstring'и приведены к правилу «код читается сам». Module-docstring'и
  сжаты до одной строки назначения, из docstring'ов функций убраны обоснования и история (они живут
  в `docs/superpowers/specs/`), инлайн-комментарии остались только там, где код без них выглядит
  ошибкой: порядок `setattr` в записи лайниджа, ленивый импорт `airflow.exceptions`, реэкспорты под
  `monkeypatch`, подавление `S314` и ограничение глубины подстановки `${var}`. Пять имён
  переименованы, чтобы снять нужду в комментарии: `variable._cfg` → `read_config`,
  `variable._validate` → `validate_config`, `callback._write` → `_write_lineage`,
  `probe._Outcome` → `_EndpointOutcome`, `hadoop_conf._expand` → `_expand_vars`. Поведение,
  сигнатуры и тексты сообщений не изменились.
```

- [ ] **Step 4: Финальная проверка**

Run: `python -m pytest airflow/config/tests -q`
Expected: `203 passed`.

Run: `grep -rn "variable\._cfg\|variable\._validate\b" --include="*.py" --include="*.bat" airflow tests`
Expected: пусто.

- [ ] **Step 5: Коммит**

```bash
git add CHANGELOG.md docs/superpowers/specs/2026-08-02-ol-policy-cache-removal-design.md docs/superpowers/plans/2026-08-02-ol-policy-cache-removal.md
git commit -m "docs: record ol_policy comment diet and sync renamed symbols"
```
