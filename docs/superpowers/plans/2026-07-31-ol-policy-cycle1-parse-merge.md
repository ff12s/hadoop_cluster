# OpenLineage Policy Cycle 1 (Ревизия 3): мердж на парсе, значения с рендера — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Привести `airflow/config/ol_policy` к формату Variable `{enabled, spark_conf, openlineage_jar}`, сняв чтение Variable и HDFS-зонда с парса на рендер, при этом сохранив мердж `spark.extraListeners` и jar'ов с тем, что задал DAG.

**Architecture:** Парс (`inject_openlineage`) регистрирует макрос и собирает строки: склеивает DAG-каналы jar'ов через `merge_jars`, выбирает канал передачи DAG-значения (`_dag_channel`) и пишет вызовы макроса в `conf` и в атрибут `jars`. Ноль обращений к Variable/метастору/HDFS. Рендер (`ol_macro`) читает Variable (`_cfg` → `_validate_cfg`), гоняет probe jar'а и оформляет своё значение под выбранный канал (`_emit`). Итог jar-мерджа живёт в атрибуте `jars`, а не в `conf["spark.jars"]`.

**Tech Stack:** Python 3.10 stdlib (контейнер Airflow 2.6.3), тесты гоняются на dev-боксе Python 3.8/3.11 без установленного Airflow, `pytest`, `jinja2` 3.1 (только в сквозном тесте через `DAG.get_template_env()`), `apache-airflow-providers-apache-spark` 4.1.1/4.10.0 (приватная `_conf`/`_jars` и публичная `conf`/`jars` раскладки).

**Спека:** `docs/superpowers/specs/2026-07-30-ol-policy-variable-sparkconf-design.md`, **Ревизия 3** (коммит `bd12629`). Ревизия 2 была реализована и откачена — коммиты сохранены на ветке `backup/ol-cycle1-2026-07-31`.

## Global Constraints

- **Русский** — комментарии, docstring'и, тексты логов, `CHANGELOG.md`. **Английский** — идентификаторы, имена ENV и **сообщения коммитов** (одна строка, без `Co-Authored-By` и любой AI-атрибуции).
- **Полные аннотации типов** на каждой функции, включая `-> None`. **Никакого `typing.Any`.** Docstring'и в формате PyCharm reST: `:param:`, `:return:`, `:raises:`.
- `ol_policy` **не импортирует Airflow на уровне модуля** — только внутри функций. Набор тестов обязан запускаться без установленного Airflow.
- **Тексты сообщений пишутся inline** в местах вызова логгера. Констант `_MSG_*` не заводить: дедуп `warn_once` опирается на ключ-кортеж, а различимость текстов проверяется по фактическому `caplog`.
- **Инвариант 6/7:** на парсе ноль обращений к Variable, метастору и HDFS. Запрещены в `inject_openlineage`, `operator_attrs`, `_level_forced`, `lineage_forced`, `_clean`, `merge_jars`, `merge_listeners`, `_dag_channel`, `_macro_call`, `jar_path`, `passthrough_exceptions`: `Variable.get`, `urlopen`, `jar_available`, `_probe*`, `_query_endpoint`. Единственные места чтения — `_cfg` и `_resolve_jar`, оба на рендере.
- **Инвариант 14:** итог jar-мерджа пишется в **атрибут `jars`**. Запись в `conf["spark.jars"]` запрещена — при заданном DAG'ом `jars=` Spark этот ключ игнорирует.
- **Инвариант 17:** разделитель принадлежит макросу. Собранная на парсе строка не содержит запятой, соседствующей с вызовом макроса.
- Все команды `pytest` запускаются из `airflow/config`.

## File Structure

| Путь | Ответственность |
| --- | --- |
| `airflow/config/ol_policy/__init__.py` | Политика целиком: парс (`apply_policy`, `inject_openlineage`, `_dag_channel`, `_macro_call`), рендер (`ol_macro`, `_cfg`, `_validate_cfg`, `_emit`, `_resolve_jar`), зонд (`jar_available` и ниже), состояние (`reset_state`). |
| `airflow/config/ol_policy/utils.py` | Чистые утилиты без Airflow: `now`, `task_dag`, `dag_and_task_ids`, `_jar_items`, `merge_jars`, **новая `merge_listeners`**. |
| `airflow/config/ol_policy/logger.py` | `warn_once` с дедупом по ключу-кортежу. Без изменений. |
| `airflow/config/ol_policy/hadoop_conf.py`, `handlers.py` | Резолвер эндпоинтов WebHDFS и `NoEndpointsError`. Без изменений. |
| `airflow/config/tests/test_ol_policy.py` | Набор тестов политики. Сейчас **untracked** — Task 1 вводит его в git. |
| `airflow/config/tests/conftest.py` | Дубли операторов обеих раскладок, `DummyDag`, фикстуры `layout` / `variable` / `_reset_policy_state`, хелпер `warnings_of`. Без изменений. |
| `airflow/scripts/start-airflow.sh` | Сидинг Variable литералами (Task 11). |
| `env_example`, `docker-compose.yml` | Удаление `OPENLINEAGE_URL` / `NAMESPACE` / `JAR` (Task 11). |
| `README.md`, `tests/README.md`, `CHANGELOG.md` | Документация (Task 12). |

## Task Decomposition Map

- Task 1 — baseline: тестовый файл в git, `merge_jars` реэкспортирован, `reset_state` чинит утечку `_passthrough_cache`, удалены тесты отменённых решений.
- Tasks 2–7 — рендер снизу вверх: `merge_listeners` → `_cfg` → `_validate_cfg` → `_emit` → `ol_macro` → `_resolve_jar`.
- Tasks 8–9 — парс: `_dag_channel`/`_macro_call`, затем переписанный `inject_openlineage`.
- Task 10 — сквозные тесты на живом Jinja и тесты-доказательства инвариантов парса.
- Tasks 11–12 — операционка и документация.

**Ожидаемая динамика падений.** База, измерено 2026-07-31: `41 failed, 145 passed`. Task 1 снимает 19 — шесть удалением тестов отменённых решений, восемь починкой утечки `_passthrough_cache`, пять реэкспортом `merge_jars`. Дальше каждая задача гасит свою группу; после Task 10 набор зелёный целиком.

Числа падений в шагах ниже — расчёт от этой базы, а не измерение. Расхождение на пару тестов само по себе не ошибка; ошибка — если после задачи падает тест **не из её группы**. Именно это и проверяйте.

---

## Task 1: Baseline — тестовый файл в git, реэкспорт, утечка `_passthrough_cache`

**Files:**
- Modify: `airflow/config/ol_policy/__init__.py`
- Modify: `airflow/config/tests/test_ol_policy.py`
- Delete: `docs/superpowers/plans/2026-07-30-ol-policy-variable-sparkconf.md`

**Interfaces:**
- Consumes: `utils.merge_jars`, `logger._warned`, модульные `_cfg`, `_jar_memo`, `_passthrough_cache`.
- Produces: `ol_policy.merge_jars` (реэкспорт для тестов), `reset_state() -> None`, сбрасывающий также `_passthrough_cache`.

- [ ] **Step 1: Убедиться, что тестовый файл не в git**

Run: `git status --porcelain airflow/config/tests/`
Expected: строка `?? airflow/config/tests/` — каталог целиком untracked. Файл `test_ol_policy.py` восстановлен с ветки `backup/ol-cycle1-2026-07-31`; в истории он появился только в откаченных коммитах.

- [ ] **Step 2: Прогнать набор и записать базу**

Run: `cd airflow/config && python -m pytest tests/test_ol_policy.py -q --tb=no`
Expected: `41 failed, 145 passed`.

- [ ] **Step 3: Показать утечку `_passthrough_cache`**

Run: `cd airflow/config && python -m pytest tests/test_ol_policy.py -q -k "resolve_local or passthrough" --tb=no`
Expected: `7 failed, 4 passed` — тесты `test_passthrough_*` падают в паре с `resolve_local`-тестами.

Run: `cd airflow/config && python -m pytest tests/test_ol_policy.py -q -k "passthrough" --tb=no`
Expected: все зелёные. Разница доказывает утечку: `passthrough_exceptions()` кэширует результат в модульной `_passthrough_cache`, тесты подменяют `airflow.*` в `sys.modules`, а `reset_state()` этот кэш не сбрасывает.

- [ ] **Step 4: Починить `reset_state` и добавить реэкспорт `merge_jars`**

В `airflow/config/ol_policy/__init__.py` заменить тело `reset_state` (строки 551-562) на:

```python
def reset_state() -> None:
    """Сбрасывает всё модульное состояние политики.

    Зовётся фикстурой ``_reset_policy_state`` (conftest.py) до и после каждого
    теста: дедупликация warning'ов, кэш ``_cfg``, мемо зонда и кэш классов
    исключений переживают границу теста и без сброса смешали бы результаты.

    :return: None.
    """
    global _passthrough_cache
    logger._warned.clear()
    _cfg.cache_clear()
    _jar_memo.clear()
    _passthrough_cache = None
```

В конец файла добавить:

```python
# Реэкспорт утилит: тесты и вызывающий код обращаются к ним через пакет политики.
merge_jars = utils.merge_jars
```

- [ ] **Step 5: Удалить тесты отменённых решений**

Из `airflow/config/tests/test_ol_policy.py` удалить целиком:

- `test_resolve_local_returns_string_when_present`
- `test_resolve_local_returns_none_when_absent`
- `test_resolve_local_returns_none_when_airflow_unavailable`

  Причина: `airflow.plugins_manager.get_template_locals` не существует (спека §2, Ревизия 3). Функция `_resolve_local` не будет реализована никогда.

- `test_probe_warning_texts_are_pairwise_distinct`
- `test_cfg_reasons_are_distinguishable`

  Причина: оба сравнивают константы `_MSG_*`, которых по Ревизии 3 не будет. Замена — тест по фактическому `caplog` в Task 10.

- `test_macro_listener_merges_with_dag_csv`

  Причина: монкипатчит `_resolve_local`. Замена — тесты каналов `dag_cur` в Task 6.

- [ ] **Step 6: Прогнать набор**

Run: `cd airflow/config && python -m pytest tests/test_ol_policy.py -q --tb=no`
Expected: около `22 failed, 158 passed`. Обязательное условие — **все** `test_passthrough_*` и `test_merge_jars_*` зелёные, а каждое оставшееся падение принадлежит группе одной из Tasks 2-9 (`merge_listeners`, `cfg`, `validate_cfg`, `macro`, `listener_constant`, `foreign_listener`, `inject`, `jar`).

- [ ] **Step 7: Удалить план Ревизии 2**

```bash
rm docs/superpowers/plans/2026-07-30-ol-policy-variable-sparkconf.md
```

Файл untracked и описывает отменённую архитектуру; его присутствие рядом с этим планом — прямая ловушка для следующего исполнителя.

- [ ] **Step 8: Commit**

```bash
git add airflow/config/tests/ airflow/config/ol_policy/__init__.py
git commit -m "test(ol_policy): track policy test suite; reset_state clears passthrough cache"
```

---

## Task 2: `merge_listeners` в `utils.py`

**Files:**
- Modify: `airflow/config/ol_policy/utils.py`
- Modify: `airflow/config/ol_policy/__init__.py`
- Test: `airflow/config/tests/test_ol_policy.py`

**Interfaces:**
- Consumes: `utils._jar_items(value: object) -> list[str]` — сплит по запятой, не режущий значения с `{{` / `{%`.
- Produces: `utils.merge_listeners(dag_cur: object, our_listener: object) -> str` и реэкспорт `ol_policy.merge_listeners`.

- [ ] **Step 1: Прогнать существующие RED-тесты**

Тесты `test_merge_listeners_*` уже лежат в наборе (7 штук: порядок, дедуп, только-DAG, только-наш, оба пустых, две параметризации на Jinja).

Run: `cd airflow/config && python -m pytest tests/test_ol_policy.py -q -k "merge_listeners" --tb=line`
Expected: `7 failed` с `AttributeError: module 'ol_policy' has no attribute 'merge_listeners'`.

- [ ] **Step 2: Реализовать `merge_listeners`**

В `airflow/config/ol_policy/utils.py` после `merge_jars` добавить:

```python
def merge_listeners(dag_cur: object, our_listener: object) -> str:
    """Склеивает CSV-лист listener'ов DAG-уровня с классом из Variable.

    Правила те же, что у ``merge_jars``: пустые элементы отбрасываются,
    значение с Jinja не режется по запятой, порядок сохраняется, дубликаты
    убираются. DAG-listener'ы идут первыми, наш — последним: дедуп защищает
    от двух инстансов одного листенера и, как следствие, от дублирующихся
    событий лайниджа.

    :param dag_cur: значение ``conf["spark.extraListeners"]``, каким его задал DAG.
    :param our_listener: класс listener'а из ``spark_conf["spark.extraListeners"]``.
    :return: список классов через запятую; "" если оба источника пусты.
    """
    merged: list[str] = []
    for source in (dag_cur, our_listener):
        for item in _jar_items(source):
            if item not in merged:
                merged.append(item)
    return ",".join(merged)
```

В `airflow/config/ol_policy/__init__.py` рядом с реэкспортом `merge_jars` добавить:

```python
merge_listeners = utils.merge_listeners
```

- [ ] **Step 3: Прогнать тесты ветки**

Run: `cd airflow/config && python -m pytest tests/test_ol_policy.py -q -k "merge_listeners or merge_jars" --tb=short`
Expected: PASS.

- [ ] **Step 4: Прогнать набор**

Run: `cd airflow/config && python -m pytest tests/test_ol_policy.py -q --tb=no`
Expected: около `15 failed` — группа `merge_listeners` погашена целиком, новых падений нет.

- [ ] **Step 5: Commit**

```bash
git add airflow/config/ol_policy/utils.py airflow/config/ol_policy/__init__.py
git commit -m "feat(ol_policy): merge_listeners merges DAG listener CSV with the Variable one"
```

---

## Task 3: `_cfg` принимает новую форму Variable

**Files:**
- Modify: `airflow/config/ol_policy/__init__.py:218-250`
- Test: `airflow/config/tests/test_ol_policy.py`

**Interfaces:**
- Consumes: `airflow.models.Variable.get` (ленивый импорт), `logger.warn_once`.
- Produces: `_cfg() -> dict[str, object] | None` под `functools.lru_cache(maxsize=1)`, принимающий только `{enabled: bool, spark_conf: dict, openlineage_jar: str}`.

- [ ] **Step 1: Прогнать RED-тест старой формы**

Тест `test_cfg_rejects_old_shape` уже в наборе.

Run: `cd airflow/config && python -m pytest tests/test_ol_policy.py::test_cfg_rejects_old_shape -q --tb=short`
Expected: FAIL — `_cfg` возвращает dict вместо `None`.

- [ ] **Step 2: Заменить проверку формы в `_cfg`**

В `airflow/config/ol_policy/__init__.py` заменить последние строки `_cfg` (блок с `auth` и `return parsed`) на:

```python
    if "auth" in parsed:
        logger.warn_once(("auth",), "OpenLineage: ключ 'auth' в Variable не поддерживается и не подставляется")
    # Форма проверяется здесь, содержимое полей — в _validate_cfg: тут решается,
    # тот ли это документ вообще, там — годится ли он для включения лайниджа.
    shape_ok = (
        isinstance(parsed.get("enabled"), bool)
        and isinstance(parsed.get("spark_conf"), dict)
        and isinstance(parsed.get("openlineage_jar"), str)
    )
    if not shape_ok:
        logger.warn_once(
            ("bad-shape",),
            "OpenLineage выключен: Variable openlineage_config должна иметь ключи "
            "enabled (bool), spark_conf (object), openlineage_jar (str)",
        )
        return None
    return parsed
```

- [ ] **Step 3: Обновить тест пустого объекта**

Тест `test_cfg_returns_empty_dict_for_empty_object` описывает отменённое поведение: пустой объект больше не годен. Заменить его тело на:

```python
def test_cfg_rejects_empty_object(variable: Callable[..., SimpleNamespace], caplog: pytest.LogCaptureFixture) -> None:
    """Пустой JSON-объект — не годная форма Variable."""
    variable(raw="{}")

    assert ol_policy._cfg() is None
    assert any("enabled (bool)" in message for message in warnings_of(caplog))
```

Переименовать сам тест в `test_cfg_rejects_empty_object` (старое имя больше не описывает поведение).

- [ ] **Step 4: Прогнать тесты ветки**

Run: `cd airflow/config && python -m pytest tests/test_ol_policy.py -q -k "cfg" --tb=short`
Expected: PASS для `test_cfg_rejects_old_shape`, `test_cfg_rejects_empty_object`, `test_cfg_returns_dict`, тестов недоступной Variable и битого JSON.

- [ ] **Step 5: Прогнать набор**

Run: `cd airflow/config && python -m pytest tests/test_ol_policy.py -q --tb=no`
Expected: падения только в группах `macro`, `listener_constant`, `validate_cfg`, `force`, `foreign_listener`.

- [ ] **Step 6: Commit**

```bash
git add airflow/config/ol_policy/__init__.py airflow/config/tests/test_ol_policy.py
git commit -m "feat(ol_policy): _cfg accepts only {enabled, spark_conf, openlineage_jar}"
```

---

## Task 4: `_validate_cfg()` — один агрегированный warning на процесс

**Files:**
- Modify: `airflow/config/ol_policy/__init__.py`
- Modify: `airflow/config/tests/test_ol_policy.py`

**Interfaces:**
- Consumes: `_cfg()`, `_clean(value, *, require_scheme=False) -> str`.
- Produces: `_validate_cfg() -> dict[str, object] | None` под `functools.lru_cache(maxsize=1)`, **без аргументов**.

- [ ] **Step 1: Привести существующие RED-тесты к контракту без аргументов**

В наборе лежат `test_validate_cfg_aggregates_missing_fields` и `test_validate_cfg_runs_once_per_process`. Второй монкипатчит `_validate_cfg` функцией, принимающей `cfg`. Заменить его тело на:

```python
def test_validate_cfg_runs_once_per_process(
    variable: Callable[..., SimpleNamespace], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Четыре вызова ol_macro подряд — один проход _validate_cfg (мемо)."""
    variable(raw=json.dumps({
        "enabled": True,
        "spark_conf": {
            "spark.extraListeners": "io.example.L",
            "spark.openlineage.transport.url": "http://m:5000",
            "spark.openlineage.namespace": "ns",
        },
        "openlineage_jar": "hdfs://n:9000/o.jar",
    }))
    calls = {"n": 0}
    original = ol_policy._validate_cfg

    def _counted() -> object:
        calls["n"] += 1
        return original()

    monkeypatch.setattr(ol_policy, "_validate_cfg", _counted)
    monkeypatch.setattr(ol_policy, "jar_available", lambda jar_uri, path: True)

    ol_policy.ol_macro("listener")
    ol_policy.ol_macro("url")
    ol_policy.ol_macro("namespace")
    ol_policy.ol_macro("jar")

    assert calls["n"] == 4
    assert original.cache_info().misses == 1
```

Счётчик считает вызовы обёртки (их четыре — по одному на макрос), а `cache_info().misses == 1` доказывает, что тело отработало один раз. Прежняя формулировка `calls["n"] == 1` была неверна: `lru_cache` живёт на `original`, а не на обёртке.

- [ ] **Step 2: Прогнать RED**

Run: `cd airflow/config && python -m pytest tests/test_ol_policy.py -q -k "validate_cfg" --tb=line`
Expected: `2 failed` — `AttributeError: module 'ol_policy' has no attribute '_validate_cfg'`.

- [ ] **Step 3: Реализовать `_validate_cfg`**

В `airflow/config/ol_policy/__init__.py` сразу после `_cfg` добавить:

```python
@functools.lru_cache(maxsize=1)
def _validate_cfg() -> dict[str, object] | None:
    """Проверяет годность Variable один раз на процесс: недостающие поля — одним warning'ом.

    Аргументов нет намеренно: под ``lru_cache`` они хэшируются, а разобранный
    конфиг — dict, и любой вызов упал бы с ``TypeError: unhashable type``.

    :return: конфиг из ``_cfg``, либо None, если он непригоден для включения лайниджа.
    """
    cfg = _cfg()
    if cfg is None:
        return None
    spark_conf_obj: object = cfg.get("spark_conf", {})
    spark_conf: dict[str, object] = spark_conf_obj if isinstance(spark_conf_obj, dict) else {}
    missing: list[str] = []
    if not _clean(spark_conf.get("spark.extraListeners")):
        missing.append("spark_conf.spark.extraListeners (непустая строка)")
    if not _clean(spark_conf.get("spark.openlineage.transport.url"), require_scheme=True):
        missing.append("spark_conf.spark.openlineage.transport.url (http/https URL)")
    if not _clean(spark_conf.get("spark.openlineage.namespace")):
        missing.append("spark_conf.spark.openlineage.namespace (непустая строка)")
    jar_uri = cfg.get("openlineage_jar")
    if not (isinstance(jar_uri, str) and jar_uri.strip()):
        missing.append("openlineage_jar (hdfs://... URI)")
    if missing:
        logger.warn_once(
            ("var-incomplete",),
            "OpenLineage не включён: Variable openlineage_config неполна: %s",
            ", ".join(missing),
        )
        return None
    return cfg
```

- [ ] **Step 4: Добавить сброс кэша в `reset_state`**

В `reset_state` после `_cfg.cache_clear()` добавить строку:

```python
    _validate_cfg.cache_clear()
```

- [ ] **Step 5: Прогнать тесты ветки**

Run: `cd airflow/config && python -m pytest tests/test_ol_policy.py -q -k "validate_cfg" --tb=short`
Expected: PASS обоих.

- [ ] **Step 6: Commit**

```bash
git add airflow/config/ol_policy/__init__.py airflow/config/tests/test_ol_policy.py
git commit -m "feat(ol_policy): _validate_cfg aggregates missing fields once per process"
```

---

## Task 5: `_emit` — единственное место, где решается разделитель

**Files:**
- Modify: `airflow/config/ol_policy/__init__.py`
- Modify: `airflow/config/tests/test_ol_policy.py`

**Interfaces:**
- Consumes: `utils.merge_listeners`, `utils.merge_jars`, `logger` (модуль `.logger`, у которого есть функция `info`; если её нет — использовать `from .logger import logger as _logger` и `_logger.info`).
- Produces: `_emit(value: str, dag_cur: str | None, merge: Callable[[object, object], str], key: str) -> str`.

- [ ] **Step 1: Написать падающие тесты**

Добавить в `airflow/config/tests/test_ol_policy.py` рядом с тестами `merge_listeners`:

```python
def test_emit_without_dag_value_returns_value_as_is() -> None:
    """Канал '': DAG молчал — возвращаем значение без разделителя."""
    assert ol_policy._emit("io.ol.L", "", ol_policy.merge_listeners, "spark.extraListeners") == "io.ol.L"


def test_emit_with_literal_merges_and_dedups() -> None:
    """Канал-литерал: полный мердж с дедупом, DAG-значения первыми."""
    merged = ol_policy._emit("io.ol.L", "com.example.A,io.ol.L", ol_policy.merge_listeners, "spark.extraListeners")

    assert merged == "com.example.A,io.ol.L"


def test_emit_with_none_channel_prefixes_comma() -> None:
    """Канал None: слева уже стоит текст DAG'а — дописываем через запятую."""
    assert ol_policy._emit("io.ol.L", None, ol_policy.merge_listeners, "spark.extraListeners") == ",io.ol.L"


def test_emit_uses_the_merge_it_was_given() -> None:
    """Ветка jar использует свой мердж — сплит и дедуп по тем же правилам."""
    merged = ol_policy._emit("hdfs://n:9000/o.jar", "a.jar", ol_policy._merge_jars_pair, "spark.jars")

    assert merged == "a.jar,hdfs://n:9000/o.jar"
```

- [ ] **Step 2: Прогнать RED**

Run: `cd airflow/config && python -m pytest tests/test_ol_policy.py -q -k "emit" --tb=line`
Expected: `4 failed` — `AttributeError: module 'ol_policy' has no attribute '_emit'`.

- [ ] **Step 3: Реализовать `_emit`**

Импорт в шапке `airflow/config/ol_policy/__init__.py` дополнить:

```python
from typing import Callable, Tuple, Type
```

Функцию добавить перед `ol_macro`:

```python
def _emit(value: str, dag_cur: str | None, merge: Callable[[object, object], str], key: str) -> str:
    """Оформляет наше значение под тот канал, которым парс передал DAG-значение.

    Единственное место, где решается разделитель: пустой результат макроса не
    должен оставлять в conf висячую запятую.

    :param value: наше значение из Variable, уже прошедшее ``_clean``.
    :param dag_cur: канал, выбранный парсом: ``""`` — DAG молчал, строка —
        безопасный литерал DAG-значения, ``None`` — текст DAG'а стоит слева.
    :param merge: ``utils.merge_listeners`` либо ``utils.merge_jars``.
    :param key: имя ключа conf для лога.
    :return: строка для подстановки на месте вызова макроса.
    """
    if dag_cur is None:
        _logger.info("ol_policy: %s дописан к DAG-значению, дедуп невозможен: %s", key, value)
        return f",{value}"
    if not dag_cur:
        _logger.info("ol_policy: %s подмешан: %s", key, value)
        return value
    merged = merge(dag_cur, value)
    _logger.info("ol_policy: %s мердж: %s", key, merged)
    return merged
```

`_logger` уже импортирован в шапке модуля строкой `from .logger import logger as _logger`.

`utils.merge_jars` объявлена как `merge_jars(current, conf_jars, jar)` — три аргумента, поэтому под тип `merge` в `_emit` она не подходит. Добавить рядом с `_emit` узкую обёртку **с отдельным именем** (одноимённая двухаргументная функция затеняла бы трёхаргументную и путала бы читателя):

```python
def _merge_jars_pair(dag_cur: object, our_jar: object) -> str:
    """Мердж двух источников jar'ов — форма, которую ждёт ``_emit``.

    Третий канал (``conf["spark.jars"]``) склеен с атрибутом ``jars`` ещё на
    парсе, поэтому на рендере источников ровно два.

    :param dag_cur: склеенные на парсе jar'ы DAG'а.
    :param our_jar: URI openlineage-spark jar'а.
    :return: список jar'ов через запятую, без дубликатов, с сохранением порядка.
    """
    return utils.merge_jars(dag_cur, None, our_jar if isinstance(our_jar, str) else "")
```

Реэкспорты в конце модуля остаются трёхаргументными и неизменными: `merge_jars = utils.merge_jars` (из Task 1), `merge_listeners = utils.merge_listeners` (из Task 2).

- [ ] **Step 4: Прогнать тесты ветки**

Run: `cd airflow/config && python -m pytest tests/test_ol_policy.py -q -k "emit or merge_jars or merge_listeners" --tb=short`
Expected: PASS. Существующие `test_merge_jars_*` зовут трёхаргументную `ol_policy.merge_jars` — их править не нужно, реэкспорт остался прежним.

- [ ] **Step 5: Commit**

```bash
git add airflow/config/ol_policy/__init__.py airflow/config/tests/test_ol_policy.py
git commit -m "feat(ol_policy): _emit formats the value for the channel chosen at parse time"
```

---

## Task 6: `ol_macro(field, forced, dag_cur)` — ветки listener / url / namespace, снос `LISTENER`

**Files:**
- Modify: `airflow/config/ol_policy/__init__.py:253-277`
- Modify: `airflow/config/tests/test_ol_policy.py`

**Interfaces:**
- Consumes: `_validate_cfg()`, `_clean`, `_emit`, `utils.merge_listeners`.
- Produces: `ol_macro(field: str, forced: bool | None = None, dag_cur: str | None = "") -> str`. Значение по умолчанию `dag_cur` — **пустая строка**, не `None`: вызов без третьего аргумента означает «DAG молчал».

- [ ] **Step 1: Написать падающие тесты каналов**

Добавить в набор:

```python
def _variable_full(variable: Callable[..., SimpleNamespace], listener: str = "io.ol.L") -> None:
    """Сидирует годную Variable нового формата.

    :param variable: фикстура подмены Variable.
    :param listener: класс listener'а, который окажется в spark_conf.
    :return: None.
    """
    variable(raw=json.dumps({
        "enabled": True,
        "spark_conf": {
            "spark.extraListeners": listener,
            "spark.openlineage.transport.url": "http://marquez:5000",
            "spark.openlineage.namespace": "hadoop-cluster",
        },
        "openlineage_jar": "hdfs://namenode:9000/o.jar",
    }))


def test_macro_listener_without_dag_value(variable: Callable[..., SimpleNamespace]) -> None:
    """Канал '': listener берётся из Variable и возвращается без разделителя."""
    _variable_full(variable)

    assert ol_policy.ol_macro("listener", None, "") == "io.ol.L"


def test_macro_listener_merges_literal_dag_csv(variable: Callable[..., SimpleNamespace]) -> None:
    """Канал-литерал: DAG-listener'ы первыми, наш последним."""
    _variable_full(variable)

    merged = ol_policy.ol_macro("listener", None, "com.example.A,com.example.B")

    assert merged == "com.example.A,com.example.B,io.ol.L"


def test_macro_listener_dedups_our_class(variable: Callable[..., SimpleNamespace]) -> None:
    """DAG уже назвал наш класс — второй раз он не появляется."""
    _variable_full(variable)

    assert ol_policy.ol_macro("listener", None, "io.ol.L,com.example.A") == "io.ol.L,com.example.A"


def test_macro_listener_prefixes_comma_for_jinja_channel(variable: Callable[..., SimpleNamespace]) -> None:
    """Канал None: значение дописывается с ведущей запятой."""
    _variable_full(variable)

    assert ol_policy.ol_macro("listener", None, None) == ",io.ol.L"


def test_macro_url_wins_over_dag_value(
    variable: Callable[..., SimpleNamespace], caplog: pytest.LogCaptureFixture
) -> None:
    """OL побеждает по url; DAG-значение попадает только в лог."""
    _variable_full(variable)
    caplog.set_level(logging.INFO)

    assert ol_policy.ol_macro("url", None, "http://dag-marquez:5000") == "http://marquez:5000"
    messages = [record.getMessage() for record in caplog.records]
    assert any("dag-marquez" in message and "marquez:5000" in message for message in messages)


def test_macro_namespace_returns_variable_value(variable: Callable[..., SimpleNamespace]) -> None:
    """namespace возвращается скаляром, без разделителей."""
    _variable_full(variable)

    assert ol_policy.ol_macro("namespace", None, "") == "hadoop-cluster"


def test_listener_constant_is_gone() -> None:
    """Инвариант 12: класс listener'а не хардкодится в политике."""
    assert not hasattr(ol_policy, "LISTENER")
```

Тест `test_listener_constant_absent` из старого набора удалить — его заменяет `test_listener_constant_is_gone` (одна проверка, одно имя).

- [ ] **Step 2: Прогнать RED**

Run: `cd airflow/config && python -m pytest tests/test_ol_policy.py -q -k "macro_listener or macro_url or macro_namespace or listener_constant" --tb=line`
Expected: падения — `ol_macro` читает `cfg["url"]`, третий аргумент не принимает, `LISTENER` существует.

- [ ] **Step 3: Переписать `ol_macro` и удалить `LISTENER`**

Удалить строку `LISTENER = "io.openlineage.spark.agent.OpenLineageSparkListener"` из шапки модуля.

Заменить `ol_macro` (строки 253-277) на:

```python
def ol_macro(field: str, forced: bool | None = None, dag_cur: str | None = "") -> str:
    """Рендер-функция: единственный источник значений лайниджа. Зовётся Jinja на воркере.

    Не бросает никогда: битый конфиг обязан давать «лайниджа нет», а не падение
    рендера всей таски.

    :param field: "listener", "url", "namespace" либо "jar".
    :param forced: True — DAG форсировал включение, False — форс-выключение, None — форса нет.
    :param dag_cur: канал DAG-значения, выбранный парсом. ``""`` — DAG ключ не задавал,
        строка — безопасный литерал, ``None`` — текст DAG'а стоит слева от вызова.
        Для скаляров ``url`` и ``namespace`` — только материал конфликтного лога.
    :return: значение для подстановки; "" если лайнидж выключен или конфиг негоден.
    """
    if forced is False:
        _logger.info("ol_policy: лайнидж выключен форсом DAG-уровня")
        return ""
    cfg = _cfg()
    if cfg is None:
        return ""
    enabled = cfg.get("enabled")
    if forced is not True and enabled is not True:
        _logger.info("ol_policy: лайнидж выключен, Variable.enabled=false и форса DAG'а нет")
        return ""
    cfg = _validate_cfg()
    if cfg is None:
        return ""
    spark_conf_obj: object = cfg.get("spark_conf", {})
    spark_conf: dict[str, object] = spark_conf_obj if isinstance(spark_conf_obj, dict) else {}
    if field == "listener":
        return _emit(_clean(spark_conf.get("spark.extraListeners")), dag_cur, merge_listeners, "spark.extraListeners")
    if field == "url":
        return _scalar(_clean(spark_conf.get("spark.openlineage.transport.url"), require_scheme=True),
                       dag_cur, "spark.openlineage.transport.url")
    if field == "namespace":
        return _scalar(_clean(spark_conf.get("spark.openlineage.namespace")), dag_cur,
                       "spark.openlineage.namespace")
    if field == "jar":
        return _resolve_jar(cfg, dag_cur)
    _logger.info("ol_policy: неизвестное поле макроса %s — подстановки нет", field)
    return ""
```

Рядом с `_emit` добавить:

```python
def _scalar(value: str, dag_cur: str | None, key: str) -> str:
    """Возвращает скалярное значение lineage-ключа, логируя перебитое DAG-значение.

    Разделителя у скаляра нет: OL побеждает целиком, ключ уже перекрыт на парсе.

    :param value: значение из Variable, прошедшее ``_clean``.
    :param dag_cur: DAG-значение того же ключа либо ``None``, если оно не литерализуемо.
    :param key: имя ключа conf для лога.
    :return: значение из Variable; "" если оно негодно.
    """
    if not value:
        logger.warn_once(("bad-field",), "OpenLineage не включён: в Variable openlineage_config негодно поле %s", key)
        return ""
    if dag_cur:
        _logger.info("ol_policy: %s в DAG-conf=%s переопределяется OL-значением=%s", key, dag_cur, value)
    else:
        _logger.info("ol_policy: %s подмешан: %s", key, value)
    return value
```

Ветка `jar` зовёт `_resolve_jar`, которая появится в Task 7. До неё эта ветка не тестируется; чтобы модуль импортировался, добавить временную заглушку сразу под `_scalar`:

```python
def _resolve_jar(cfg: dict[str, object], dag_cur: str | None) -> str:
    """Заглушка Task 6: реализация зонда приезжает в Task 7.

    :param cfg: разобранный конфиг из ``_validate_cfg``.
    :param dag_cur: канал DAG-значения jar'ов.
    :return: пустая строка.
    """
    del cfg, dag_cur
    return ""
```

- [ ] **Step 4: Обновить старые тесты макроса под новую форму Variable**

Тесты `test_macro_returns_values`, `test_macro_trims_values`, `test_macro_rejects_bad_namespace`, `test_force_enables_without_enabled_flag`, `test_macro_is_silent_on_honest_off` (и любые другие, чей `variable(raw=...)` содержит ключи `"url"`/`"namespace"` верхнего уровня) переписать на новую форму: три ключа переезжают в `spark_conf`, добавляется `openlineage_jar`. Пример для `test_macro_returns_values`:

```python
def test_macro_returns_values(variable: Callable[..., SimpleNamespace]) -> None:
    """Годная Variable отдаёт listener, url и namespace из spark_conf."""
    _variable_full(variable)

    assert ol_policy.ol_macro("listener") == "io.ol.L"
    assert ol_policy.ol_macro("url") == "http://marquez:5000"
    assert ol_policy.ol_macro("namespace") == "hadoop-cluster"
```

- [ ] **Step 5: Прогнать набор**

Run: `cd airflow/config && python -m pytest tests/test_ol_policy.py -q --tb=short`
Expected: падают только тесты, завязанные на `inject_openlineage` старой схемы (`foreign_listener`, `injected_keys`, `dag_jars_survive`, `unavailable_jar_blocks_forced_injection` и подобные) — их разбирает Task 9.

- [ ] **Step 6: Commit**

```bash
git add airflow/config/ol_policy/__init__.py airflow/config/tests/test_ol_policy.py
git commit -m "feat(ol_policy): ol_macro reads spark_conf and honours the dag_cur channel; drop LISTENER"
```

---

## Task 7: `_resolve_jar` — probe на рендере

**Files:**
- Modify: `airflow/config/ol_policy/__init__.py`
- Modify: `airflow/config/tests/test_ol_policy.py`

**Interfaces:**
- Consumes: `cfg["openlineage_jar"]`, `jar_path(jar_uri) -> str | None`, `jar_available(jar_uri, path) -> bool`, `_emit`, `merge_jars` (двухаргументный адаптер из Task 5).
- Produces: `_resolve_jar(cfg: dict[str, object], dag_cur: str | None) -> str` — заменяет заглушку Task 6.

- [ ] **Step 1: Написать падающие тесты**

```python
def test_macro_jar_merges_with_dag_jars(
    variable: Callable[..., SimpleNamespace], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Канал-литерал: DAG-jar'ы первыми, наш последним."""
    _variable_full(variable)
    monkeypatch.setattr(ol_policy, "jar_available", lambda jar_uri, path: True)

    assert ol_policy.ol_macro("jar", None, "a.jar") == "a.jar,hdfs://namenode:9000/o.jar"


def test_macro_jar_alone_when_dag_silent(
    variable: Callable[..., SimpleNamespace], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Канал '': только наш jar, без разделителя."""
    _variable_full(variable)
    monkeypatch.setattr(ol_policy, "jar_available", lambda jar_uri, path: True)

    assert ol_policy.ol_macro("jar", None, "") == "hdfs://namenode:9000/o.jar"


def test_macro_jar_prefixes_comma_for_jinja_channel(
    variable: Callable[..., SimpleNamespace], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Канал None: наш jar дописывается через запятую."""
    _variable_full(variable)
    monkeypatch.setattr(ol_policy, "jar_available", lambda jar_uri, path: True)

    assert ol_policy.ol_macro("jar", None, None) == ",hdfs://namenode:9000/o.jar"


def test_macro_jar_empty_when_probe_says_no(
    variable: Callable[..., SimpleNamespace], monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Probe не подтвердил jar — пустая строка и warning, DAG-jar'ы не тронуты."""
    _variable_full(variable)
    monkeypatch.setattr(ol_policy, "jar_available", lambda jar_uri, path: False)

    assert ol_policy.ol_macro("jar", None, "a.jar") == ""
    assert any("HDFS" in message for message in warnings_of(caplog))


def test_macro_jar_rejects_uri_without_scheme(
    variable: Callable[..., SimpleNamespace], monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """URI без схемы spark-submit трактует как локальный файл — не подмешиваем."""
    variable(raw=json.dumps({
        "enabled": True,
        "spark_conf": {
            "spark.extraListeners": "io.ol.L",
            "spark.openlineage.transport.url": "http://marquez:5000",
            "spark.openlineage.namespace": "ns",
        },
        "openlineage_jar": "/opt/openlineage/o.jar",
    }))

    def _forbidden(jar_uri: str, path: str) -> bool:
        pytest.fail("зонд не должен вызываться для URI без схемы")

    monkeypatch.setattr(ol_policy, "jar_available", _forbidden)

    assert ol_policy.ol_macro("jar", None, "") == ""
    assert any("без схемы" in message for message in warnings_of(caplog))


def test_macro_jar_probes_once_per_uri(
    variable: Callable[..., SimpleNamespace], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Два вызова макроса — один поход в сеть: мемо по URI держит результат."""
    _variable_full(variable)
    probed: list[str] = []

    def _counting_probe(path: str) -> bool:
        """Считает походы в HDFS и всегда подтверждает jar.

        :param path: путь jar'а внутри HDFS.
        :return: True.
        """
        probed.append(path)
        return True

    monkeypatch.setattr(ol_policy, "_probe", _counting_probe)

    first = ol_policy.ol_macro("jar", None, "")
    second = ol_policy.ol_macro("jar", None, "")

    assert first == second == "hdfs://namenode:9000/o.jar"
    assert probed == ["/o.jar"]
```

- [ ] **Step 2: Прогнать RED**

Run: `cd airflow/config && python -m pytest tests/test_ol_policy.py -q -k "macro_jar" --tb=line`
Expected: падения — заглушка `_resolve_jar` возвращает `""` для всех случаев.

- [ ] **Step 3: Заменить заглушку реализацией**

```python
def _resolve_jar(cfg: dict[str, object], dag_cur: str | None) -> str:
    """Проверяет наличие jar'а в HDFS и оформляет URI под канал DAG-значения.

    Мемо ``jar_available`` живёт на процессе воркера: поток тасок с одним и тем же
    URI не перегаживает кластер запросами.

    :param cfg: разобранный конфиг из ``_validate_cfg``.
    :param dag_cur: канал DAG-значения jar'ов, выбранный парсом.
    :return: строка для подстановки в атрибут ``jars``; "" при любом отказе.
    """
    jar_uri_obj = cfg.get("openlineage_jar")
    jar_uri = jar_uri_obj.strip() if isinstance(jar_uri_obj, str) else ""
    if not jar_uri:
        logger.warn_once(("jar-unset",), "OpenLineage не включён: openlineage_jar в Variable не задан")
        return ""
    path = jar_path(jar_uri)
    if path is None:
        logger.warn_once(
            ("jar-malformed",),
            "OpenLineage не включён: openlineage_jar задан без схемы или без пути (%s)",
            jar_uri,
        )
        return ""
    if not jar_available(jar_uri, path):
        logger.warn_once(
            ("jar-absent",),
            "OpenLineage не включён: jar отсутствует или недоступен в HDFS (%s). "
            "Залейте его: scripts/seed-openlineage-jar.bat",
            jar_uri,
        )
        return ""
    return _emit(jar_uri, dag_cur, _merge_jars_pair, "spark.jars")
```

- [ ] **Step 4: Прогнать тесты ветки**

Run: `cd airflow/config && python -m pytest tests/test_ol_policy.py -q -k "macro_jar or probe" --tb=short`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add airflow/config/ol_policy/__init__.py airflow/config/tests/test_ol_policy.py
git commit -m "feat(ol_policy): _resolve_jar probes HDFS at render time"
```

---

## Task 8: `_dag_channel` и `_macro_call` — выбор канала на парсе

**Files:**
- Modify: `airflow/config/ol_policy/__init__.py`
- Modify: `airflow/config/tests/test_ol_policy.py`

**Interfaces:**
- Consumes: `MACRO` — имя макроса `"__openlineage_v1"`.
- Produces: `_dag_channel(value: object) -> tuple[str, str | None]` и `_macro_call(field: str, forced: str, dag_cur: str | None) -> str`.

- [ ] **Step 1: Написать падающие тесты**

```python
def test_dag_channel_empty_value() -> None:
    """Пусто, None и пробелы — DAG молчал: префикса нет, канал ''."""
    assert ol_policy._dag_channel(None) == ("", "")
    assert ol_policy._dag_channel("") == ("", "")
    assert ol_policy._dag_channel("   ") == ("", "")


def test_dag_channel_safe_literal() -> None:
    """Безопасное значение уходит литералом, префикса нет."""
    assert ol_policy._dag_channel("a.jar,b.jar") == ("", "a.jar,b.jar")


@pytest.mark.parametrize("value", ["{{ params.jars }}", "{% if x %}a.jar{% endif %}", "it's.jar", 'say"hi".jar'])
def test_dag_channel_unsafe_value_stays_in_the_string(value: str) -> None:
    """Jinja и кавычки нельзя вложить в текст вызова макроса — значение остаётся слева."""
    assert ol_policy._dag_channel(value) == (value, None)


def test_macro_call_renders_literal() -> None:
    """Литерал попадает в вызов в одинарных кавычках."""
    call = ol_policy._macro_call("listener", "none", "com.example.A")

    assert call == "{{ __openlineage_v1('listener', none, 'com.example.A') }}"


def test_macro_call_renders_none_channel() -> None:
    """Канал None рендерится как Jinja-литерал none, а не как строка 'None'."""
    assert ol_policy._macro_call("jar", "true", None) == "{{ __openlineage_v1('jar', true, none) }}"


def test_macro_call_renders_empty_channel() -> None:
    """Канал '' рендерится пустой строкой-литералом."""
    assert ol_policy._macro_call("url", "none", "") == "{{ __openlineage_v1('url', none, '') }}"
```

- [ ] **Step 2: Прогнать RED**

Run: `cd airflow/config && python -m pytest tests/test_ol_policy.py -q -k "dag_channel or macro_call" --tb=line`
Expected: `AttributeError` на обеих функциях.

- [ ] **Step 3: Реализовать обе функции**

Рядом с `MACRO` в шапке модуля добавить:

```python
# Значение с этими фрагментами нельзя вложить литералом в текст вызова макроса:
# Jinja порвётся на вложенных скобках, кавычка — на самой кавычке.
_UNSAFE_FOR_LITERAL = ("{{", "{%", "'", '"')
```

Перед `inject_openlineage` добавить:

```python
def _dag_channel(value: object) -> tuple[str, str | None]:
    """Выбирает канал, которым DAG-значение доедет до макроса.

    Каналов три, и решение принимает парс — единственный, кто видит исходное
    значение: на рендере прочитать его нечем.

    :param value: значение ключа conf либо атрибута оператора, как его задал DAG.
    :return: пара ``(prefix, dag_cur)``. ``prefix`` ставится в строку перед вызовом
        макроса, ``dag_cur`` уходит третьим аргументом макроса.
    """
    text = value.strip() if isinstance(value, str) else ""
    if not text:
        return "", ""
    if any(marker in text for marker in _UNSAFE_FOR_LITERAL):
        return text, None
    return "", text


def _macro_call(field: str, forced: str, dag_cur: str | None) -> str:
    """Собирает текст вызова макроса для подстановки в conf.

    :param field: имя ветки макроса.
    :param forced: ``"true"`` либо ``"none"`` — Jinja-литерал форса.
    :param dag_cur: канал DAG-значения из ``_dag_channel``.
    :return: строка вида ``{{ __openlineage_v1('field', none, 'value') }}``.
    """
    literal = "none" if dag_cur is None else f"'{dag_cur}'"
    return f"{{{{ {MACRO}('{field}', {forced}, {literal}) }}}}"
```

- [ ] **Step 4: Прогнать тесты ветки**

Run: `cd airflow/config && python -m pytest tests/test_ol_policy.py -q -k "dag_channel or macro_call" --tb=short`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add airflow/config/ol_policy/__init__.py airflow/config/tests/test_ol_policy.py
git commit -m "feat(ol_policy): _dag_channel picks how the DAG value reaches the macro"
```

---

## Task 9: `inject_openlineage` — сборка строк на парсе, снос env-зонда

**Files:**
- Modify: `airflow/config/ol_policy/__init__.py:463-521`
- Modify: `airflow/config/tests/test_ol_policy.py`

**Interfaces:**
- Consumes: `operator_attrs`, `lineage_forced`, `utils.task_dag`, `utils.merge_jars` (трёхаргументная, из `utils`), `_dag_channel`, `_macro_call`, `MACRO`, `ol_macro`.
- Produces: `inject_openlineage(task: object) -> None`, который пишет `conf` и атрибут `jars`, не читая Variable/HDFS. Удаляет `ol_conf_template`, `foreign_listener`, `_OUR_LISTENERS`.

- [ ] **Step 1: Написать падающие тесты**

```python
def test_inject_writes_macro_calls_not_values(layout: SimpleNamespace) -> None:
    """Парс кладёт в conf вызовы макроса, а не значения из Variable."""
    dag = DummyDag()
    task = layout.cls(dag=dag)

    ol_policy.inject_openlineage(task)

    conf = getattr(task, layout.conf)
    assert conf["spark.openlineage.transport.url"] == "{{ __openlineage_v1('url', none, '') }}"
    assert conf["spark.openlineage.namespace"] == "{{ __openlineage_v1('namespace', none, '') }}"
    assert conf["spark.extraListeners"] == "{{ __openlineage_v1('listener', none, '') }}"
    assert conf["spark.openlineage.transport.type"] == "http"
    assert dag.user_defined_macros[ol_policy.MACRO] is ol_policy.ol_macro


def test_inject_puts_jar_merge_into_the_jars_attribute(layout: SimpleNamespace) -> None:
    """Инвариант 14: итог мерджа jar'ов живёт в атрибуте jars, а не в conf."""
    dag = DummyDag()
    task = layout.cls(dag=dag, jars="a.jar", conf={"spark.jars": "b.jar"})

    ol_policy.inject_openlineage(task)

    assert getattr(task, layout.jars) == "{{ __openlineage_v1('jar', none, 'a.jar,b.jar') }}"
    assert getattr(task, layout.conf)["spark.jars"] == "b.jar"


def test_inject_keeps_dag_listener_as_literal(layout: SimpleNamespace) -> None:
    """DAG-CSV listener'ов уходит литералом — дедуп на рендере возможен."""
    dag = DummyDag()
    task = layout.cls(dag=dag, conf={"spark.extraListeners": "com.example.A"})

    ol_policy.inject_openlineage(task)

    conf = getattr(task, layout.conf)
    assert conf["spark.extraListeners"] == "{{ __openlineage_v1('listener', none, 'com.example.A') }}"


def test_inject_leaves_jinja_dag_value_in_the_string(layout: SimpleNamespace) -> None:
    """DAG-значение со своей Jinja остаётся слева от вызова макроса."""
    dag = DummyDag()
    task = layout.cls(dag=dag, jars="{{ params.jars }}")

    ol_policy.inject_openlineage(task)

    assert getattr(task, layout.jars) == "{{ params.jars }}{{ __openlineage_v1('jar', none, none) }}"


def test_inject_does_not_touch_foreign_conf_keys(layout: SimpleNamespace) -> None:
    """Ключи DAG-conf вне lineage-набора остаются как были."""
    dag = DummyDag()
    task = layout.cls(dag=dag, conf={"spark.app.name": "demo", "spark.executor.cores": "2"})

    ol_policy.inject_openlineage(task)

    conf = getattr(task, layout.conf)
    assert conf["spark.app.name"] == "demo"
    assert conf["spark.executor.cores"] == "2"


def test_inject_never_reads_variable(layout: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    """Инвариант 6: парс не ходит в метастор."""
    models = types.ModuleType("airflow.models")

    class _Forbidden:
        """Дубль Variable, роняющий тест при любом обращении."""

        @staticmethod
        def get(*args: object, **kwargs: object) -> object:
            """Валит тест: на парсе Variable читать нельзя.

            :param args: позиционные аргументы настоящего API.
            :param kwargs: именованные аргументы настоящего API.
            :return: ничего не возвращает.
            """
            pytest.fail("Variable.get вызван на парсе")

    models.Variable = _Forbidden
    package = types.ModuleType("airflow")
    package.models = models
    monkeypatch.setitem(sys.modules, "airflow", package)
    monkeypatch.setitem(sys.modules, "airflow.models", models)

    ol_policy.inject_openlineage(layout.cls(dag=DummyDag()))


def test_inject_never_touches_network(layout: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    """Инвариант 7: парс не делает сетевых вызовов."""
    def _forbidden(*args: object, **kwargs: object) -> object:
        """Валит тест: зонд на парсе запрещён.

        :param args: позиционные аргументы.
        :param kwargs: именованные аргументы.
        :return: ничего не возвращает.
        """
        pytest.fail("сетевой вызов на парсе")

    monkeypatch.setattr(ol_policy, "jar_available", _forbidden)
    monkeypatch.setattr(ol_policy, "urlopen", _forbidden)

    ol_policy.inject_openlineage(layout.cls(dag=DummyDag()))


def test_inject_ignores_openlineage_jar_env(layout: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    """URI jar'а живёт в Variable; переменной окружения политика не знает."""
    monkeypatch.setenv("OPENLINEAGE_JAR", "hdfs://namenode:9000/from-env.jar")
    task = layout.cls(dag=DummyDag())

    ol_policy.inject_openlineage(task)

    assert "from-env.jar" not in getattr(task, layout.jars)
```

`DummyDag` и `types`/`sys` уже импортированы в тестовом модуле; если `DummyDag` импортируется из `conftest`, добавить его в существующий импорт.

- [ ] **Step 2: Прогнать RED**

Run: `cd airflow/config && python -m pytest tests/test_ol_policy.py -q -k "inject_" --tb=line`
Expected: падения — текущий `inject_openlineage` читает `OPENLINEAGE_JAR`, зовёт `jar_available` и пишет значения, а не вызовы макроса.

- [ ] **Step 3: Переписать `inject_openlineage`**

Заменить функцию целиком на:

```python
def inject_openlineage(task: object) -> None:
    """Навешивает OpenLineage на проверенную Spark-таску: макрос и строки в conf.

    Порядок гейтов нормативен: форс-выключение проверяется раньше всего, поэтому
    выключивший лайнидж DAG уходит нетронутым. Значения лайниджа сюда не попадают —
    на парсе собираются только строки с вызовами макроса, а Variable и HDFS
    читаются на рендере.

    :param task: экземпляр ``SparkSubmitOperator``; мутируется на месте.
    :return: None.
    """
    dag_id, task_id = utils.dag_and_task_ids(task)

    attrs = operator_attrs(task)
    if attrs is None:
        logger.warn_once(("unknown-layout", dag_id, task_id), "OpenLineage не включён: незнакомая раскладка атрибутов оператора (%s.%s)", dag_id, task_id)
        return

    forced = lineage_forced(task)
    if forced is False:
        return

    dag = utils.task_dag(task)
    if dag is None:
        logger.warn_once(("no-dag", dag_id, task_id), "OpenLineage не включён: таска не привязана к DAG, макрос положить некуда (%s)", task_id)
        return

    # Макрос кладётся в DAG политикой намеренно: это единственный способ отложить
    # чтение Variable до рендера таски, ничего не требуя от автора DAG'а. Чужим
    # считается только объект, который не является нашей функцией, — иначе вторая
    # таска файла увидела бы чужим то, что положила первая.
    macros = dict(getattr(dag, "user_defined_macros", None) or {})
    if MACRO in macros and macros[MACRO] is not ol_macro:
        logger.warn_once(("macro-taken", dag_id, task_id), "OpenLineage не включён: имя макроса %s занято чужим объектом (%s.%s)", MACRO, dag_id, task_id)
        return

    forced_literal = "true" if forced is True else "none"
    cur_conf = dict(getattr(task, attrs.conf) or {})

    listener_prefix, listener_cur = _dag_channel(cur_conf.get("spark.extraListeners"))
    # Оба канала jar'ов известны здесь и склеиваются до макроса: на рендере
    # прочитать их будет нечем.
    jars_prefix, jars_cur = _dag_channel(utils.merge_jars(getattr(task, attrs.jars), cur_conf.get("spark.jars"), ""))
    # Для скаляров префикс отбрасывается: OL побеждает целиком, дописывать текст
    # DAG'а слева значило бы нарушить это правило.
    _, url_cur = _dag_channel(cur_conf.get("spark.openlineage.transport.url"))
    _, namespace_cur = _dag_channel(cur_conf.get("spark.openlineage.namespace"))

    if listener_cur is None or jars_cur is None:
        logger.warn_once(
            ("jinja-channel", dag_id, task_id),
            "OpenLineage: DAG-значение содержит Jinja — дедуп значения OL невозможен (%s.%s)",
            dag_id,
            task_id,
        )

    macros[MACRO] = ol_macro
    dag.user_defined_macros = macros
    setattr(task, attrs.jars, jars_prefix + _macro_call("jar", forced_literal, jars_cur))
    setattr(task, attrs.conf, {
        **cur_conf,
        "spark.extraListeners": listener_prefix + _macro_call("listener", forced_literal, listener_cur),
        "spark.openlineage.transport.type": "http",
        "spark.openlineage.transport.url": _macro_call("url", forced_literal, url_cur),
        "spark.openlineage.namespace": _macro_call("namespace", forced_literal, namespace_cur),
        "spark.openlineage.columnLineage.datasetLineageEnabled": "true",
    })
```

- [ ] **Step 4: Удалить отменённые функции и импорт `os`**

Удалить из `airflow/config/ol_policy/__init__.py`:

- `ol_conf_template` (целиком),
- `_OUR_LISTENERS`,
- `foreign_listener` (целиком),
- `import os` из шапки, если после правки он больше не используется — проверить `grep -n "os\." airflow/config/ol_policy/__init__.py`.

- [ ] **Step 5: Разобрать тесты старой схемы**

Удалить тесты, описывающие отменённое поведение:

- `test_foreign_listener_blocks_injection` и `test_our_own_template_is_not_foreign` — гейта чужого listener'а больше нет, мердж делает это лучше.
- `test_injected_keys_are_exactly_five` — набор ключей проверяет `test_inject_writes_macro_calls_not_values`.
- `test_unavailable_jar_blocks_forced_injection`, `test_unset_jar_env_skips_probe`, `test_malformed_jar_env_skips_probe`, `test_many_tasks_cause_one_network_trip` — зонд ушёл с парса; его поведение проверяют `test_macro_jar_*` из Task 7.
- `test_dag_conf_wins` — правило перевернулось: теперь OL побеждает по lineage-ключам (`test_macro_url_wins_over_dag_value`).
- `test_no_mutation_when_assembly_fails`, `test_interrupted_mutation_leaves_jar_without_listener` — порядок мутаций больше не нормативен: на парсе нет ни одной операции, способной оборваться на сетевом вызове.

Обновить, а не удалять:

- `test_dag_jars_survive` — теперь DAG-jar'ы обязаны сохраниться **внутри** вызова макроса: `assert "a.jar" in getattr(task, layout.jars)`.
- `test_task_force_on_beats_dag_force_off` — вместо проверки значения conf проверять, что в вызовах макроса стоит форс: `assert "'listener', true" in getattr(task, layout.conf)["spark.extraListeners"]`.
- `test_spark_task_is_injected_through_apply_policy` — проверять наличие вызова макроса, а не значения listener'а.

- [ ] **Step 6: Прогнать набор**

Run: `cd airflow/config && python -m pytest tests/test_ol_policy.py -q --tb=short`
Expected: PASS целиком.

- [ ] **Step 7: Commit**

```bash
git add airflow/config/ol_policy/__init__.py airflow/config/tests/test_ol_policy.py
git commit -m "refactor(ol_policy): inject_openlineage builds macro calls at parse time, drops env jar probe"
```

---

## Task 10: Сквозные тесты — живой Jinja и различимость причин

**Files:**
- Modify: `airflow/config/tests/test_ol_policy.py`

**Interfaces:**
- Consumes: `airflow.models.dag.DAG` (только в этом файле и только в тестах, помеченных пропуском при отсутствии Airflow), `ol_policy.inject_openlineage`, `ol_policy.ol_macro`.
- Produces: тестов не потребляет никто — это конечные проверки.

- [ ] **Step 1: Написать тест полного цикла на живом Jinja**

```python
airflow_dag = pytest.importorskip("airflow.models.dag", reason="нужен установленный Airflow")


def test_full_cycle_renders_expected_command_values(
    variable: Callable[..., SimpleNamespace], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Парс собрал строки, живой Jinja их отрендерил — значения на месте, запятых лишних нет."""
    _variable_full(variable)
    monkeypatch.setattr(ol_policy, "jar_available", lambda jar_uri, path: True)
    dag = airflow_dag.DAG(dag_id="render", schedule=None, start_date=None)
    task = PublicLayoutOperator(dag=dag, jars="a.jar", conf={"spark.extraListeners": "com.example.A"})

    ol_policy.inject_openlineage(task)
    env = dag.get_template_env()
    rendered_conf = {key: env.from_string(value).render() for key, value in task.conf.items()}
    rendered_jars = env.from_string(task.jars).render()

    assert rendered_conf["spark.extraListeners"] == "com.example.A,io.ol.L"
    assert rendered_conf["spark.openlineage.transport.url"] == "http://marquez:5000"
    assert rendered_conf["spark.openlineage.namespace"] == "hadoop-cluster"
    assert rendered_jars == "a.jar,hdfs://namenode:9000/o.jar"


def test_full_cycle_leaves_no_trailing_comma_when_lineage_is_off(
    variable: Callable[..., SimpleNamespace]
) -> None:
    """Инвариант 17: выключённый лайнидж не оставляет висячей запятой."""
    variable(raw=json.dumps({
        "enabled": False,
        "spark_conf": {
            "spark.extraListeners": "io.ol.L",
            "spark.openlineage.transport.url": "http://marquez:5000",
            "spark.openlineage.namespace": "ns",
        },
        "openlineage_jar": "hdfs://namenode:9000/o.jar",
    }))
    dag = airflow_dag.DAG(dag_id="render_off", schedule=None, start_date=None)
    task = PublicLayoutOperator(dag=dag, jars="a.jar", conf={"spark.extraListeners": "com.example.A"})

    ol_policy.inject_openlineage(task)
    env = dag.get_template_env()

    assert env.from_string(task.jars).render() == "a.jar"
    assert env.from_string(task.conf["spark.extraListeners"]).render() == "com.example.A"
```

`PublicLayoutOperator` берётся из `conftest`; `dag.user_defined_macros` заполняет сама политика, поэтому подставлять макрос руками не нужно.

- [ ] **Step 2: Написать тест различимости причин отказа**

```python
def test_failure_reasons_are_pairwise_distinct(
    variable: Callable[..., SimpleNamespace], caplog: pytest.LogCaptureFixture
) -> None:
    """Каждая причина отказа звучит в логе по-своему — иначе расследование слепое."""
    scenarios: dict[str, object] = {
        "no-var": None,
        "bad-json": "{",
        "not-object": "[1, 2, 3]",
        "bad-shape": '{"enabled": true}',
        "var-incomplete": json.dumps({"enabled": True, "spark_conf": {}, "openlineage_jar": ""}),
    }
    collected: list[str] = []
    for raw in scenarios.values():
        ol_policy.reset_state()
        caplog.clear()
        variable(raw=raw)
        ol_policy.ol_macro("listener")
        collected.extend(warnings_of(caplog))

    assert len(collected) == len(set(collected)), f"неразличимые тексты: {collected}"
    assert len(collected) == len(scenarios)
```

- [ ] **Step 3: Прогнать новые тесты**

Run: `cd airflow/config && python -m pytest tests/test_ol_policy.py -q -k "full_cycle or failure_reasons" --tb=short`
Expected: PASS. Если Airflow на машине не установлен — тесты `full_cycle` пропускаются через `importorskip`, `failure_reasons` идёт всегда.

- [ ] **Step 4: Прогнать весь набор и оба файла тестов**

Run: `cd airflow/config && python -m pytest tests/ -q --tb=short`
Expected: PASS целиком, включая `tests/test_hadoop_conf.py`.

- [ ] **Step 5: Commit**

```bash
git add airflow/config/tests/test_ol_policy.py
git commit -m "test(ol_policy): end-to-end Jinja render and pairwise-distinct failure reasons"
```

---

## Task 11: Операционка — сидинг литералами, снос ENV-переменных

**Files:**
- Modify: `airflow/scripts/start-airflow.sh`
- Modify: `env_example`
- Modify: `docker-compose.yml`

**Interfaces:**
- Consumes: текущий heredoc сидинга Variable и блок `x-versions` в compose.
- Produces: Variable `openlineage_config` формы `{enabled, spark_conf, openlineage_jar}`, собранная литералами.

- [ ] **Step 1: Посмотреть текущее состояние сидинга**

Run: `grep -n "OPENLINEAGE" airflow/scripts/start-airflow.sh env_example docker-compose.yml`
Expected: список мест, где читаются `OPENLINEAGE_URL`, `OPENLINEAGE_NAMESPACE`, `OPENLINEAGE_JAR`, и где остаются `OPENLINEAGE_VERSION` / `OPENLINEAGE_CONFIG_RESEED`.

- [ ] **Step 2: Заменить тело heredoc в `start-airflow.sh`**

Питоновский блок, собирающий JSON, должен собирать литералы:

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

Чтения `os.environ` для url/namespace/jar убрать. Проверку идемпотентности (`airflow variables get` + `OPENLINEAGE_CONFIG_RESEED`) не трогать. В шапочном комментарии скрипта убрать упоминание `OPENLINEAGE_*` как источника значений и написать, что конфиг лайниджа правится в Admin → Variables.

- [ ] **Step 3: Почистить `env_example`**

Удалить строки `OPENLINEAGE_NAMESPACE=…`, `OPENLINEAGE_URL=…`, `OPENLINEAGE_JAR=…`. Оставить `OPENLINEAGE_VERSION` (по нему собираются имена jar-файлов) и закомментированный `OPENLINEAGE_CONFIG_RESEED=false`. Комментарий над ним заменить на:

```
# Конфиг лайниджа живёт в Airflow Variable openlineage_config (Admin -> Variables),
# а не в .env. OPENLINEAGE_CONFIG_RESEED=true пересевает Variable дефолтами при старте —
# устаревший рычаг, оставлен на случай миграции.
```

- [ ] **Step 4: Почистить `docker-compose.yml`**

Из блока `x-versions` и из окружения сервиса airflow удалить `OPENLINEAGE_NAMESPACE`, `OPENLINEAGE_URL`, `OPENLINEAGE_JAR`. Оставить `OPENLINEAGE_VERSION` и `OPENLINEAGE_CONFIG_RESEED`.

- [ ] **Step 5: Проверить, что ссылок не осталось**

Run: `grep -rn "OPENLINEAGE_JAR\|OPENLINEAGE_URL\|OPENLINEAGE_NAMESPACE" airflow scripts tests docker-compose.yml env_example README.md`
Expected: совпадений нет нигде, кроме `README.md` и `tests/README.md` — их правит Task 12. Если совпадение нашлось в `airflow/config/ol_policy/`, значит Task 9 не дочистил.

- [ ] **Step 6: Проверить синтаксис compose**

Run: `docker compose -f docker-compose.yml config --quiet`
Expected: пустой вывод, код возврата 0.

- [ ] **Step 7: Commit**

```bash
git add airflow/scripts/start-airflow.sh env_example docker-compose.yml
git commit -m "chore(ol_policy): seed Variable with literals, drop OPENLINEAGE_URL/NAMESPACE/JAR env"
```

---

## Task 12: Документация

**Files:**
- Modify: `README.md`
- Modify: `tests/README.md`
- Modify: `CHANGELOG.md`

**Interfaces:**
- Consumes: итоговое поведение из Tasks 1-11.
- Produces: описание формата Variable и правил мерджа для человека, который будет это эксплуатировать.

- [ ] **Step 1: Обновить раздел OpenLineage в `README.md`**

Внести:

- формат Variable `openlineage_config`: `{enabled, spark_conf, openlineage_jar}` с примером JSON из `start-airflow.sh`;
- политика читает из `spark_conf` ровно три ключа: `spark.extraListeners`, `spark.openlineage.transport.url`, `spark.openlineage.namespace`; остальные ключи `spark_conf` в conf таски не попадают;
- `spark.extraListeners` **мерджится** с CSV, который задал DAG: DAG-listener'ы первыми, OL-listener последним, дубликаты убираются;
- `spark.openlineage.transport.url` и `spark.openlineage.namespace` — **OL побеждает** над DAG-conf;
- jar'ы мерджатся; итог живёт в `--jars`, а не в `spark.jars`;
- переменных `OPENLINEAGE_URL` / `OPENLINEAGE_NAMESPACE` / `OPENLINEAGE_JAR` больше нет; правка конфига — в Admin → Variables, действует со следующего запуска таски без рестарта;
- полное имя класса listener'а живёт только в `start-airflow.sh` и в этом README — в коде политики его нет;
- если DAG кладёт в `jars` или `spark.extraListeners` собственное Jinja-выражение, дедуп значения OL невозможен — политика пишет об этом warning.

- [ ] **Step 2: Обновить `tests/README.md`**

В списке проверок `test-airflow.bat` заменить пункты про OL-ключи на:

- «Cluster policy: в фактически собранной команде `spark-submit` присутствуют оба jar'а — DAG'овский и openlineage — и оба listener'а, если DAG задал свой»;
- «DAG, передавший свой `spark.extraListeners`, всё равно получает OL-listener из Variable»;
- «Правка Variable `openlineage_config` подхватывается без рестарта, со следующего запуска таски».

В разделе `test-policy.bat` пункт про JSON-объект Variable оставить как есть.

- [ ] **Step 3: Добавить запись в `CHANGELOG.md`**

В секцию `## [Unreleased]` (создать, если её нет) добавить:

```markdown
### Изменено

- OpenLineage cluster policy: Variable `openlineage_config` хранит `{enabled, spark_conf, openlineage_jar}`
  вместо плоского `{enabled, url, namespace}`. URI openlineage-jar'а переехал из переменной окружения
  `OPENLINEAGE_JAR` в Variable, а зонд HDFS — с парса DAG-файла на рендер таски: шедулер и DAG-bag
  больше не читают ни метастор, ни сеть. `spark.extraListeners` и jar'ы мерджатся с тем, что задал DAG
  (дедуп, DAG-значения первыми), а `spark.openlineage.transport.url` и `spark.openlineage.namespace`
  берутся из Variable даже если DAG задал свои. Итог jar-мерджа пишется в `--jars`: при заданном
  DAG'ом `jars=` Spark игнорирует `spark.jars`. Хардкод класса listener'а из политики удалён.

### Удалено

- Переменные окружения `OPENLINEAGE_URL`, `OPENLINEAGE_NAMESPACE`, `OPENLINEAGE_JAR`.
  Конфиг лайниджа правится в Admin → Variables. `OPENLINEAGE_CONFIG_RESEED` остаётся как рычаг пересева.
```

- [ ] **Step 4: Прогнать весь набор ещё раз**

Run: `cd airflow/config && python -m pytest tests/ -q --tb=short`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add README.md tests/README.md CHANGELOG.md
git commit -m "docs: describe the new openlineage_config Variable shape and merge rules"
```

---

## Self-Review

**1. Покрытие спеки.**

| Раздел спеки | Задача |
| --- | --- |
| §2 факт «`get_template_locals` не существует» | Task 1 (удаление тестов `_resolve_local`); функция не реализуется нигде |
| §2 факт «`--jars` вытесняет `spark.jars`» | Task 9 (итог в атрибут `jars`), тест `test_inject_puts_jar_merge_into_the_jars_attribute` |
| §3 таблица «что где решается» | Tasks 7, 9; тесты `test_inject_never_reads_variable`, `test_inject_never_touches_network` |
| §4.1 сборка строк на парсе | Tasks 8, 9 |
| §4.2 контракт `ol_macro` и `_validate_cfg` | Tasks 4, 6 |
| §4.3 мердж на парсе, значение с рендера | Tasks 5, 6, 7, 9 |
| §4.4 таблица конфликтов ключей | Tasks 6, 7 (каналы `dag_cur` для listener и jar, `_scalar` для url/namespace) |
| §4.5 таблица истинности | Task 6 (`forced`/`enabled`), Task 7 (probe), Task 10 (выключенный лайнидж целиком) |
| §5.1 форма Variable | Task 3 |
| §5.2 удаляемые ENV | Task 11 |
| §6 зонд на рендере | Task 7 |
| §7 сидинг литералами | Task 11 |
| §8 инварианты 6, 7, 12, 13, 14, 15, 16, 17 | Tasks 6 (12), 6 (13), 9 (14), 5/6/7 (15), 2/6 (16), 5/10 (17), 9 (6 и 7) |
| §9 тесты | Tasks 2-10 |
| §10 изменения по файлам | Tasks 1-12 |
| §12 фикс утечки `_passthrough_cache` | Task 1 |

**2. Плейсхолдеры.** Каждый шаг содержит либо готовый код, либо точную команду с ожидаемым выводом. Формулировок «добавить обработку ошибок», «написать тесты для вышеописанного», «аналогично Task N» нет: код повторён там, где нужен.

**3. Согласованность типов.**

- `_dag_channel(value: object) -> tuple[str, str | None]` — объявлена в Task 8, потребляется в Task 9.
- `_macro_call(field: str, forced: str, dag_cur: str | None) -> str` — Task 8, потребляется в Task 9. `forced` — строка `"true"`/`"none"`, а не bool: это Jinja-литерал.
- `_emit(value: str, dag_cur: str | None, merge: Callable[[object, object], str], key: str) -> str` — Task 5, потребляется в Tasks 6 и 7.
- `_scalar(value: str, dag_cur: str | None, key: str) -> str` — Task 6, потребляется там же.
- `_resolve_jar(cfg: dict[str, object], dag_cur: str | None) -> str` — заглушка в Task 6, реализация в Task 7, сигнатура одна.
- `ol_macro(field: str, forced: bool | None = None, dag_cur: str | None = "") -> str` — Task 6; Task 9 передаёт третьим аргументом результат `_dag_channel`.
- `utils.merge_listeners(dag_cur: object, our_listener: object) -> str` — Task 2; двухаргументная, подходит под тип `merge` в `_emit`.
- `_merge_jars_pair(dag_cur: object, our_jar: object) -> str` — обёртка из Task 5 поверх трёхаргументной `utils.merge_jars(current, conf_jars, jar)`, нужна только чтобы подойти под тип `merge` в `_emit`. Имя отдельное намеренно: одноимённая двухаргументная функция затеняла бы реэкспорт `merge_jars = utils.merge_jars` и ломала бы существующие тесты. В Task 9 парс зовёт трёхаргументную `utils.merge_jars(...)` напрямую.
- `_validate_cfg() -> dict[str, object] | None` — Task 4, без аргументов; потребляется в Task 6.

**4. Каверзы, отмеченные в задачах.**

- `_emit` принимает двухаргументный мердж, а `utils.merge_jars` — трёхаргументная. Task 5 добавляет обёртку `_merge_jars_pair` под отдельным именем; реэкспорт `merge_jars` остаётся трёхаргументным, существующие `test_merge_jars_*` не трогаются.
- `test_validate_cfg_runs_once_per_process` в наборе написан под отменённый контракт с аргументом; Task 4 Step 1 переписывает его и объясняет, почему проверка идёт по `cache_info().misses`.
- Ветка `jar` в `ol_macro` появляется в Task 6 раньше своей реализации, поэтому Task 6 ставит заглушку с той же сигнатурой, а Task 7 её заменяет.
