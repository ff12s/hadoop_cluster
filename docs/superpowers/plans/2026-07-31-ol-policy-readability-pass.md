# ol_policy Readability Pass Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Убрать из пакета `airflow/config/ol_policy` дубли и нетипизированные контракты, не меняя поведения ни в одной ветке политики.

**Architecture:** Пакет уже разложен по фазам жизненного цикла (`parse` → `render`, плюс `variable`, `probe`, `operator`, `utils`). Правки идут внутри этих границ: результат валидации Variable становится `NamedTuple` вместо сырого dict, три копии одного мерджа сводятся к одной функции, слот демон-потока и раскладка атрибутов оператора получают типы. Ни один модуль не начинает импортировать Airflow на уровне модуля.

**Tech Stack:** Python 3.10, Apache Airflow 2.6.3, `apache-airflow-providers-apache-spark` 4.1.1, pytest. Только стандартная библиотека внутри пакета.

## Global Constraints

- Комментарии, docstring'и и сообщения логов — на русском. Идентификаторы, имена ключей conf и имена ENV — английские.
- Полная аннотация типов на каждой функции и методе, включая `-> None`. `typing.Any` запрещён.
- Docstring в формате PyCharm reST: summary, затем `:param:`, `:return:`, `:raises:` там, где функция бросает.
- Длина строки — 120 символов.
- Ни один модуль пакета не импортирует Airflow на уровне модуля: импорт только внутри функций.
- Поведение не меняется. Тексты warning'ов не переписываются: их попарную различимость проверяет `test_failure_reasons_are_pairwise_distinct`.
- Прогон после каждой задачи: `python -m pytest airflow/config/tests -q` из корня `hadoop_cluster`.
- Цикл рефакторинга вместо RED→GREEN: тесты зелены до правки, правятся вместе с кодом в том же шаге, зелены после. Красная фаза есть только у задачи 0 — там чинится сам набор.

## File Structure

| Файл | Ответственность | Задачи |
|---|---|---|
| `airflow/config/tests/test_ol_policy.py` | набор тестов политики | 0, 1, 2 |
| `airflow/config/ol_policy/utils.py` | общие хелперы: время, id таски, мердж CSV | 1, 5 |
| `airflow/config/ol_policy/render.py` | рендер-фаза, значения лайниджа | 1, 2, 5 |
| `airflow/config/ol_policy/parse.py` | парс-фаза, сборка строк | 1, 5 |
| `airflow/config/ol_policy/__init__.py` | точка входа и реэкспорты | 1, 5 |
| `airflow/config/ol_policy/variable.py` | чтение и проверка Variable | 2, 5 |
| `airflow/config/ol_policy/probe.py` | зонд jar в HDFS | 3, 5 |
| `airflow/config/ol_policy/operator.py` | совместимость раскладок оператора | 4, 5 |
| `airflow/config/ol_policy/logger.py` | дедуплицированные warning'и | 5 |

---

### Task 0: Вернуть набору тестов способность падать

**Files:**
- Modify: `airflow/config/tests/test_ol_policy.py:2167` и пять тестов ниже (`test_full_cycle_renders_expected_command_values`, `test_full_cycle_leaves_no_trailing_comma_when_lineage_is_off`, `test_full_cycle_injects_nothing_when_jar_is_absent`, `test_full_cycle_keeps_dag_values_when_jar_is_absent`)

**Interfaces:**
- Consumes: `_airflow_installed()` — уже определён в этом же файле на строке ~1993.
- Produces: полный набор тестов, собираемый без установленного Airflow.

- [ ] **Step 1: Зафиксировать текущее число собираемых тестов**

Run: `python -m pytest airflow/config/tests --collect-only -q 2>&1 | tail -3`
Expected: `12 tests collected` — весь `test_ol_policy.py` пропущен.

- [ ] **Step 2: Убрать модульный importorskip**

Удалить строку 2167 целиком:

```python
airflow_dag = pytest.importorskip("airflow.models.dag", reason="нужен установленный Airflow")
```

- [ ] **Step 3: Навесить пер-тестовый гейт на четыре сквозных теста**

Каждый из четырёх тестов, использовавших `airflow_dag`, получает декоратор и локальный импорт. Пятый тест раздела (`test_failure_reasons_are_pairwise_distinct`) Airflow не использует — его не трогать. Образец для `test_full_cycle_renders_expected_command_values`, остальные три правятся точно так же:

```python
@pytest.mark.skipif(not _airflow_installed(), reason="нужен установленный Airflow")
def test_full_cycle_renders_expected_command_values(
    variable: Callable[..., SimpleNamespace], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Парс собрал строки, живой Jinja их отрендерил — значения на месте, запятых лишних нет."""
    from airflow.models import dag as airflow_dag

    _variable_full(variable)
    monkeypatch.setattr(ol_policy.probe, "jar_available", lambda jar_uri, path: True)
    dag = airflow_dag.DAG(dag_id="render", schedule=None, start_date=None)
```

- [ ] **Step 4: Проверить, что набор собрался целиком**

Run: `python -m pytest airflow/config/tests -q 2>&1 | tail -3`
Expected: собрано на порядок больше 12 тестов, все зелёные, скипов — ровно пять (четыре сквозных плюс `test_template_renders_in_sandboxed_environment`). Это число — эталон для всех следующих задач.

- [ ] **Step 5: Commit**

```bash
git add airflow/config/tests/test_ol_policy.py
git commit -m "fix(tests): stop module-level importorskip from skipping the whole ol_policy suite"
```

---

### Task 1: Один мердж CSV вместо трёх копий

**Files:**
- Modify: `airflow/config/ol_policy/utils.py:36-88`
- Modify: `airflow/config/ol_policy/render.py:33-68,139,185`
- Modify: `airflow/config/ol_policy/parse.py:107`
- Modify: `airflow/config/ol_policy/__init__.py:31-44,96-98`
- Modify: `airflow/config/tests/test_ol_policy.py` (обращения к `ol_policy.merge_jars`, `ol_policy.merge_listeners`, `ol_policy.render._merge_jars_pair`)

**Interfaces:**
- Consumes: ничего из предыдущих задач.
- Produces: `utils.merge_csv(*sources: object) -> str`, реэкспорт `ol_policy.merge_csv`. Имена `merge_jars`, `merge_listeners`, `render._merge_jars_pair` перестают существовать.

- [ ] **Step 1: Переписать мердж в utils.py**

Заменить `_jar_items`, `merge_jars` и `merge_listeners` на два определения:

```python
def _csv_items(value: object) -> list[str]:
    """Элементы CSV-значения одного источника.

    Значение с Jinja не режется по запятой: атрибут ``jars`` и ключи conf
    шаблонизируются, и разбиение порвало бы выражение с запятой внутри.

    :param value: значение атрибута оператора либо ключа conf.
    :return: список непустых элементов; для не-строки — пустой список.
    """
    if not isinstance(value, str):
        return []
    if "{{" in value or "{%" in value:
        return [value.strip()] if value.strip() else []
    return [item.strip() for item in value.split(",") if item.strip()]


def merge_csv(*sources: object) -> str:
    """Склеивает CSV-источники в порядке перечисления, убирая дубликаты.

    Одни правила для jar'ов и для listener'ов: пустые элементы отбрасываются,
    порядок сохраняется, наше значение идёт последним. Дедуп listener'ов защищает
    от двух инстансов одного класса и, как следствие, от задвоенных событий лайниджа.

    :param sources: значения источников: атрибут ``jars``, ключи conf, наше значение.
    :return: элементы через запятую; "" если все источники пусты.
    """
    return ",".join(dict.fromkeys(item for source in sources for item in _csv_items(source)))
```

- [ ] **Step 2: Убрать адаптер из render.py**

Удалить `_merge_jars_pair` целиком. В `_resolve_jar` заменить вызов:

```python
    return _emit(jar_uri, dag_cur, utils.merge_csv, "spark.jars")
```

В `_emit` сигнатура параметра `merge` становится `Callable[..., str]`, docstring параметра — `:param merge: ``utils.merge_csv``.`. Ветка `listener` в `ol_macro` передаёт туда же `utils.merge_csv`.

- [ ] **Step 3: Обновить вызов в parse.py**

```python
    jars_prefix, jars_cur = _dag_channel(utils.merge_csv(getattr(task, attrs.jars), cur_conf.get("spark.jars")))
```

Третий аргумент `""` уходит: `merge_csv` вариадична.

- [ ] **Step 4: Обновить реэкспорты пакета**

В `__init__.py` в `__all__` заменить `"merge_jars"` и `"merge_listeners"` на `"merge_csv"`; в блоке реэкспорта внизу файла оставить одну строку `merge_csv = utils.merge_csv`.

- [ ] **Step 5: Обновить тесты**

Найти все обращения и заменить механически: `ol_policy.merge_listeners(a, b)` → `ol_policy.merge_csv(a, b)`; `ol_policy.merge_jars(a, b, c)` → `ol_policy.merge_csv(a, b, c)`; `ol_policy.render._merge_jars_pair` в `test_emit_uses_the_merge_it_was_given` → `ol_policy.merge_csv`. Полный список обращений снять командой, а не выборкой:

```bash
grep -n "merge_jars\|merge_listeners" airflow/config/tests/test_ol_policy.py
```

Ожидаемо 14 обращений (9 `merge_listeners`, 4 `merge_jars`, 1 `_merge_jars_pair`); если их больше — править все, счётчик здесь справочный.

- [ ] **Step 6: Прогон**

Run: `python -m pytest airflow/config/tests -q 2>&1 | tail -3`
Expected: то же число тестов, что в задаче 0, все зелёные.

- [ ] **Step 7: Commit**

```bash
git add airflow/config/ol_policy airflow/config/tests/test_ol_policy.py
git commit -m "refactor(ol_policy): collapse three CSV merges into utils.merge_csv"
```

---

### Task 2: Типизированный `variable.Config` вместо сырого dict на рендере

**Files:**
- Modify: `airflow/config/ol_policy/variable.py:83-115`
- Modify: `airflow/config/ol_policy/render.py:89-190`
- Modify: `airflow/config/tests/test_ol_policy.py` (обращения к `ol_policy.variable._validate_cfg`)

**Interfaces:**
- Consumes: `utils.merge_csv` из задачи 1.
- Produces: `variable.Config` — `NamedTuple` с полями `listener: str`, `url: str`, `namespace: str`, `jar_uri: str`; `variable._validate_cfg() -> Config | None`. `variable._cfg()` не меняется: он остаётся `dict[str, object] | None`, потому что `ol_macro` читает по нему `enabled` до валидации, а тесты патчат именно его.

- [ ] **Step 1: Ввести Config и вернуть его из _validate_cfg**

В `variable.py` добавить импорт `from typing import NamedTuple` и объявить тип рядом с `VARIABLE`:

```python
class Config(NamedTuple):
    """Проверенные поля Variable ``openlineage_config``: все непустые, уже очищенные."""

    listener: str
    url: str
    namespace: str
    jar_uri: str
```

`_validate_cfg` собирает значения один раз и отдаёт их же:

```python
@functools.lru_cache(maxsize=1)
def _validate_cfg() -> Config | None:
    """Проверяет годность Variable один раз на процесс: недостающие поля — одним warning'ом.

    Аргументов нет намеренно: под ``lru_cache`` они хэшируются, а разобранный
    конфиг — dict, и любой вызов упал бы с ``TypeError: unhashable type``.

    :return: проверенный конфиг либо None, если он непригоден для включения лайниджа.
    """
    cfg = _cfg()
    if cfg is None:
        return None
    spark_conf_obj: object = cfg.get("spark_conf", {})
    spark_conf: dict[str, object] = spark_conf_obj if isinstance(spark_conf_obj, dict) else {}
    config = Config(
        listener=_clean(spark_conf.get("spark.extraListeners")),
        url=_clean(spark_conf.get("spark.openlineage.transport.url"), require_scheme=True),
        namespace=_clean(spark_conf.get("spark.openlineage.namespace")),
        jar_uri=_clean(cfg.get("openlineage_jar")),
    )
    missing = [
        name
        for value, name in (
            (config.listener, "spark_conf.spark.extraListeners (непустая строка)"),
            (config.url, "spark_conf.spark.openlineage.transport.url (http/https URL)"),
            (config.namespace, "spark_conf.spark.openlineage.namespace (непустая строка)"),
            (config.jar_uri, "openlineage_jar (hdfs://... URI)"),
        )
        if not value
    ]
    if missing:
        warn_once(
            ("var-incomplete",),
            "OpenLineage не включён: Variable openlineage_config неполна: %s",
            ", ".join(missing),
        )
        return None
    return config
```

Порядок перечисления в `missing` тот же, что был, — текст warning'а не меняется.

- [ ] **Step 2: Упростить render.py под типизированный конфиг**

`_jar_ok` и `_resolve_jar` перестают разбирать dict:

```python
def _jar_ok(cfg: variable.Config, *, warn: bool = False) -> bool:
    """Подтверждён ли openlineage-jar в HDFS — общий гейт всего лайниджа.

    Инвариант 19: ``spark.extraListeners`` без jar'а на classpath роняет драйвер
    ``ClassNotFoundException``, поэтому неподтверждённый jar выключает лайнидж целиком,
    а не одну ветку ``jar``. Зонд мемоизирован по URI: четыре ветки макроса за один
    рендер стоят одного похода в сеть.

    :param cfg: проверенный конфиг из ``variable._validate_cfg``.
    :param warn: писать ли причину отказа. True только у ветки ``jar``: иначе три
        остальные ветки того же рендера продублировали бы одно сообщение.
    :return: True, если jar подтверждён в HDFS; False при любом отказе.
    """
    path = probe.jar_path(cfg.jar_uri)
    if path is None:
        if warn:
            log.warning("OpenLineage не включён: openlineage_jar задан без схемы или без пути (%s)", cfg.jar_uri)
        return False
    if not probe.jar_available(cfg.jar_uri, path):
        if warn:
            log.warning(
                "OpenLineage не включён: jar отсутствует или недоступен в HDFS (%s). "
                "Залейте его: scripts/seed-openlineage-jar.bat",
                cfg.jar_uri,
            )
        return False
    return True


def _resolve_jar(cfg: variable.Config, dag_cur: str | None) -> str:
    """Оформляет URI подтверждённого jar'а под канал DAG-значения.

    Ветка ``jar`` — единственная, которая называет причину отказа зонда: остальные три
    гейтятся тем же ``_jar_ok`` молча, чтобы один отказ не звучал четырежды.

    :param cfg: проверенный конфиг из ``variable._validate_cfg``.
    :param dag_cur: канал DAG-значения jar'ов, выбранный парсом.
    :return: строка для подстановки в атрибут ``jars``; при отказе — собственное
        значение DAG'а, а не пустая строка.
    """
    if not _jar_ok(cfg, warn=True):
        return _refusal(dag_cur)
    return _emit(cfg.jar_uri, dag_cur, utils.merge_csv, "spark.jars")
```

Хвост `ol_macro` после валидации:

```python
    config = variable._validate_cfg()
    if config is None:
        return _refusal(dag_cur)
    if field == "jar":
        return _resolve_jar(config, dag_cur)
    if field not in ("listener", "url", "namespace"):
        log.info("ol_policy: неизвестное поле макроса %s — подстановки нет", field)
        return _refusal(dag_cur)
    # Инвариант 19: нет jar'а — нет и лайниджа, отказ зонда гасит все ветки, а не одну.
    if not _jar_ok(config):
        return _refusal(dag_cur)
    if field == "listener":
        return _emit(config.listener, dag_cur, utils.merge_csv, "spark.extraListeners")
    if field == "url":
        return _scalar(config.url, dag_cur, "spark.openlineage.transport.url")
    return _scalar(config.namespace, dag_cur, "spark.openlineage.namespace")
```

Локальная переменная `cfg` из ветки `enabled` остаётся сырым dict'ом `variable._cfg()`; переменная под результат валидации называется `config`, чтобы два разных типа не делили имя.

- [ ] **Step 3: Обновить тесты, патчащие _validate_cfg**

Найти обращения:

```bash
grep -n "_validate_cfg" airflow/config/tests/test_ol_policy.py
```

Тест, подставляющий свой `_validate_cfg`, должен возвращать `ol_policy.variable.Config(...)`, а не dict. Тесты, патчащие `variable._cfg` (их большинство), не меняются: `_cfg` по-прежнему отдаёт dict.

- [ ] **Step 4: Прогон**

Run: `python -m pytest airflow/config/tests -q 2>&1 | tail -3`
Expected: то же число тестов, все зелёные.

- [ ] **Step 5: Commit**

```bash
git add airflow/config/ol_policy airflow/config/tests/test_ol_policy.py
git commit -m "refactor(ol_policy): return a typed Config from _validate_cfg"
```

---

### Task 3: Типизированный слот зонда

**Files:**
- Modify: `airflow/config/ol_policy/probe.py:68-176`

**Interfaces:**
- Consumes: ничего из предыдущих задач.
- Produces: внутренние типы `probe._Outcome`; сигнатура `probe._probe_worker(path: str, slot: list[bool | BaseException]) -> None`. Публичные `jar_path` и `jar_available` не меняются.

- [ ] **Step 1: Типизировать исход опроса эндпоинта**

Добавить `from typing import Literal` и объявить рядом с константами:

```python
_Outcome = Literal["found", "absent", "standby", "error"]
```

Сигнатура становится `def _query_endpoint(endpoint: str, path: str) -> _Outcome:`, тело не меняется.

- [ ] **Step 2: Убрать метки "ok"/"err" из слота**

```python
def _probe_worker(path: str, slot: list[bool | BaseException]) -> None:
    """Тело демон-потока зонда: кладёт в слот результат либо исключение.

    :param path: абсолютный путь jar'а в HDFS.
    :param slot: список-слот, куда кладётся ровно один элемент.
    :return: None.
    """
    try:
        slot.append(_probe(path))
    except Exception as error:
        slot.append(error)
```

Разбор в `jar_available`:

```python
    slot: list[bool | BaseException] = []
    worker = threading.Thread(target=_probe_worker, args=(path, slot), daemon=True, name="openlineage-jar-probe")
    worker.start()
    worker.join(_PROBE_DEADLINE_SEC)

    if not slot:
        available = False
        warn_once(
            ("probe-deadline",),
            "OpenLineage не включён: зонд jar не уложился в дедлайн %s с (%s)",
            _PROBE_DEADLINE_SEC,
            jar_uri,
        )
    elif isinstance(slot[0], BaseException):
        available = False
        if isinstance(slot[0], handlers.NoEndpointsError):
            warn_once(
                ("no-endpoints",),
                "OpenLineage не включён: эндпоинты WebHDFS не определены по HADOOP_CONF_DIR (%s)",
                slot[0],
            )
        else:
            warn_once(
                ("probe-error",),
                "OpenLineage не включён: не удалось определить эндпоинты WebHDFS (%s): %s",
                jar_uri,
                slot[0],
            )
    else:
        available = slot[0]
```

Тексты warning'ов и их ключи дедупликации — прежние, посимвольно.

- [ ] **Step 3: Прогон**

Run: `python -m pytest airflow/config/tests -q 2>&1 | tail -3`
Expected: то же число тестов, все зелёные.

- [ ] **Step 4: Commit**

```bash
git add airflow/config/ol_policy/probe.py
git commit -m "refactor(ol_policy): drop the string-tagged slot protocol from the jar probe"
```

---

### Task 4: `OperatorAttrs` вместо `SimpleNamespace`

**Files:**
- Modify: `airflow/config/ol_policy/operator.py:10-72`

**Interfaces:**
- Consumes: ничего из предыдущих задач.
- Produces: `operator.OperatorAttrs` — `NamedTuple` с полями `conf: str`, `jars: str`; `operator.operator_attrs(task: object) -> OperatorAttrs | None`. Обращения `attrs.conf` и `attrs.jars` в `parse.py` не меняются.

- [ ] **Step 1: Объявить тип и вернуть его**

Импорт `SimpleNamespace` уходит, `from typing import Tuple, Type` заменяется на `from typing import NamedTuple`.

```python
class OperatorAttrs(NamedTuple):
    """Имена атрибутов conf и jars конкретной раскладки оператора."""

    conf: str
    jars: str


def operator_attrs(task: object) -> OperatorAttrs | None:
    """Имена атрибутов conf и jars у этого оператора.

    Имя обязано одновременно быть в ``template_fields`` (значит, будет отрендерено)
    и существовать на объекте (значит, его читает hook). В провайдере 4.1.1 атрибуты
    приватные, в 4.10.0 — публичные, поэтому имя резолвится, а не зашивается.

    :param task: таска Airflow.
    :return: имена атрибутов либо None, если раскладка незнакома.
    """
    fields = set(getattr(task, "template_fields", ()) or ())
    resolved: dict[str, str] = {}
    for role, candidates in _ATTR_CANDIDATES.items():
        for name in candidates:
            if name in fields and hasattr(task, name):
                resolved[role] = name
                break
        else:
            return None
    return OperatorAttrs(**resolved)
```

- [ ] **Step 2: Заменить typing.Tuple/Type на встроенные**

```python
def passthrough_exceptions() -> tuple[type[BaseException], ...]:
```

Тело не меняется: `from __future__ import annotations` в файле уже есть.

- [ ] **Step 3: Прогон**

Run: `python -m pytest airflow/config/tests -q 2>&1 | tail -3`
Expected: то же число тестов, все зелёные.

- [ ] **Step 4: Commit**

```bash
git add airflow/config/ol_policy/operator.py
git commit -m "refactor(ol_policy): type the operator attribute layout as a NamedTuple"
```

---

### Task 5: Стиль и сжатие комментариев по всему пакету

**Files:**
- Modify: `airflow/config/ol_policy/utils.py`, `logger.py`, `__init__.py`, `operator.py`, `render.py`, `parse.py`, `variable.py`, `probe.py`

**Interfaces:**
- Consumes: всё из задач 1–4.
- Produces: изменений в сигнатурах нет.

- [ ] **Step 1: Дооформить utils.py и logger.py**

Оба файла получают module-docstring и `from __future__ import annotations` первой строкой кода. В `utils.py` — две пустые строки между определениями (сейчас перед `merge_csv` и `merge_listeners` по одной). В `logger.py` `from typing import Dict, Tuple` уходит, аннотации становятся `dict[tuple[str, ...], float]` и `key: tuple[str, ...]`.

```python
"""Общие хелперы политики: монотонное время, идентификаторы таски, мердж CSV-значений."""
```

```python
"""Логгер политики с дедупликацией: одна причина отказа не спамит лог на каждой таске."""
```

- [ ] **Step 2: Перенести строки длиннее 120 символов**

Снять полный список, а не выборку:

```bash
awk 'length > 120 {print FILENAME":"FNR": "length}' airflow/config/ol_policy/*.py
```

Известные места: `__init__.py` (два вызова `warn_once` в `apply_policy`), `operator.py` (два вызова `warn_once` в `_level_forced`), `render.py` (ветка `listener` в `ol_macro`). Переносить по аргументам, как уже сделано в `variable.py`.

- [ ] **Step 3: Ужать docstring'и до «почему»**

Правило: каждый факт живёт в одном месте — у своего владельца; в остальных местах ссылка на модуль, а не повтор. Обязательно остаются: инвариант 19 (`render._jar_ok`), нормативный порядок записи `jars` → `conf` (`parse.inject_openlineage`), причина демон-потока и мемо (`probe.jar_available`), причина ленивого импорта `render` внутри `inject_openlineage`, причина отсутствия аргументов у `_validate_cfg` под `lru_cache`, причина запрета Jinja в литерале (`parse._UNSAFE_FOR_LITERAL`), причина ленивой сборки кортежа исключений (`operator.passthrough_exceptions`), причина чтения `dag` через `try` (`utils.task_dag`).

Уходят: пересказ сигнатуры прозой, повтор правила «отказ возвращает значение DAG'а, а не пустую строку» в четырёх docstring'ах подряд (`render._refusal`, `_resolve_jar`, `ol_macro` — оставить в `_refusal`, в остальных ссылка), повтор «пустое значение сюда не попадает, `_validate_cfg` уже отверг» в `_scalar` и `_jar_ok` (после задачи 2 это следует из типа `Config`).

- [ ] **Step 4: Прогон**

Run: `python -m pytest airflow/config/tests -q 2>&1 | tail -3`
Expected: то же число тестов, все зелёные.

- [ ] **Step 5: Проверить, что длинных строк не осталось**

Run: `awk 'length > 120 {print FILENAME":"FNR}' airflow/config/ol_policy/*.py`
Expected: пустой вывод.

- [ ] **Step 6: Commit**

```bash
git add airflow/config/ol_policy
git commit -m "style(ol_policy): wrap long lines and trim docstrings to the why"
```

---

## Итоговая проверка

- [ ] `python -m pytest airflow/config/tests -q` — число собранных тестов равно эталону задачи 0, падений нет.
- [ ] `git diff main --stat -- airflow/config/ol_policy` — пакет стал короче, ни один модуль не приобрёл импорт Airflow на уровне модуля: `grep -n "^import airflow\|^from airflow" airflow/config/ol_policy/*.py` даёт пустой вывод.
- [ ] `CHANGELOG.md` дополнен записью о рефакторинге, если он ведётся в этом репозитории.
