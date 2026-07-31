# Разбиение пакета `ol_policy` по фазам — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Разложить `airflow/config/ol_policy/__init__.py` (791 строка, 34 top-level символа) на пять модулей по фазам жизненного цикла политики, не меняя ни одного байта поведения.

**Architecture:** Каждый модуль — одна фаза: `probe` (зонд HDFS), `variable` (чтение и валидация Airflow Variable), `operator` (совместимость раскладок провайдера и тумблер из `params`), `render` (Jinja-макрос), `parse` (сборка строк на парсе). `__init__.py` остаётся фасадом: `apply_policy`, `reset_state` и реэкспорты. Несущее ограничение — `parse.py` не импортирует ни `variable`, ни `probe`: инварианты «на парсе ноль обращений к Variable и сети» становятся видны в шапке файла.

**Tech Stack:** Python 3.8/3.11 (тесты на dev-боксе), контейнер Airflow 2.6.3 (python3.10), `pytest`, стандартная библиотека (`functools`, `threading`, `urllib`, `json`).

**Спека:** `docs/superpowers/specs/2026-07-31-ol-policy-package-split-design.md` (коммит `757db5c`).

## Global Constraints

- **Поведение не меняется.** Ни одного нового `if`, ни одного изменённого текста лога, ни одной новой ветки. Диф по логике пуст: перемещения, правка импортов, аннотация `Literal`, удаление доказанно недостижимых веток.
- **Тестовые утверждения не меняются.** Правятся только цели `monkeypatch.setattr` — 32 штуки, разложенные по таблице в Task 7.
- **Ни один модуль пакета не импортирует Airflow на уровне модуля** — только внутри функций. Набор тестов обязан запускаться без установленного Airflow.
- **Русский** — комментарии, docstring'и, тексты логов. **Английский** — идентификаторы, имена ENV, **сообщения коммитов** (одна строка, conventional-commit с прописной буквы в теле, без `Co-Authored-By` и любой AI-атрибуции).
- **Полные аннотации типов** на каждой функции, включая `-> None`. `typing.Any` запрещён. Docstring'и в формате PyCharm reST с `:param:` / `:return:`.
- Подмодули импортируют **только соседей**, никогда сам пакет: `from . import probe` — да, `import ol_policy` внутри модуля пакета — нет (цикл).
- Все команды `pytest` запускаются из `airflow/config`. Базовое состояние — **228 passed**, и это число обязано сохраняться после каждой задачи.

## File Structure

| Путь | Ответственность | Импортирует из пакета |
| --- | --- | --- |
| `airflow/config/ol_policy/__init__.py` | Фасад: `apply_policy`, `reset_state`, реэкспорты | `parse`, `render`, `probe`, `variable`, `operator`, `utils`, `logger` |
| `airflow/config/ol_policy/probe.py` | **Новый.** Зонд HDFS целиком: `jar_path`, `_is_standby`, `_query_endpoint`, `_probe`, `_probe_worker`, `jar_available`, `_jar_memo`, `_now`, `resolve_webhdfs_urls`, `_PROBE_DEADLINE_SEC`, `ENDPOINT_TIMEOUT_SEC`, `_MEMO_TTL_SEC` | `hadoop_conf`, `handlers`, `logger`, `utils` |
| `airflow/config/ol_policy/variable.py` | **Новый.** `_clean`, `_cfg`, `_validate_cfg`, `VARIABLE` | `logger` |
| `airflow/config/ol_policy/operator.py` | **Новый.** `passthrough_exceptions`, `operator_attrs`, `_spark_submit_operator`, `_looks_like_spark_submit`, `_level_forced`, `lineage_forced`, `_ATTR_CANDIDATES`, `_PASSTHROUGH_NAMES` | `logger`, `utils` |
| `airflow/config/ol_policy/render.py` | **Новый.** `_refusal`, `_emit`, `_merge_jars_pair`, `_scalar`, `_jar_ok`, `_resolve_jar`, `ol_macro` | `variable`, `probe`, `logger`, `utils` |
| `airflow/config/ol_policy/parse.py` | **Новый.** `_dag_channel`, `_macro_call`, `inject_openlineage`, `_UNSAFE_FOR_LITERAL`, `MACRO` | `operator`, `logger`, `utils` |
| `airflow/config/tests/test_ol_policy.py` | Цели 32 патчей переезжают на модули | — |
| `utils.py`, `logger.py`, `hadoop_conf.py`, `handlers.py` | Без изменений | — |

## Task Decomposition Map

Порядок продиктован графом зависимостей — сначала листья, потом их потребители, фасад последним:

- **Task 1** `probe.py` — лист, никого из пакета не тянет кроме `hadoop_conf`/`handlers`/`logger`/`utils`. Забирает 27 из 32 патчей.
- **Task 2** `variable.py` — лист.
- **Task 3** `operator.py` — лист.
- **Task 4** `render.py` — потребитель `variable` + `probe`.
- **Task 5** `parse.py` — потребитель `operator`; здесь же проверяется несущее ограничение.
- **Task 6** `__init__.py` сводится к фасаду; попутные миноры спеки §5.
- **Task 7** контрольная проверка: патчи бьют в цель, инварианты держатся, размеры файлов в норме.

---

## Task 1: Вынести зонд HDFS в `probe.py`

**Files:**
- Create: `airflow/config/ol_policy/probe.py`
- Modify: `airflow/config/ol_policy/__init__.py:44-56, 495-638`
- Modify: `airflow/config/tests/test_ol_policy.py` (27 целей патчей)

**Interfaces:**
- Consumes: `hadoop_conf.resolve_webhdfs_urls`, `handlers.NoEndpointsError`, `logger.warn_once`, `utils.now`.
- Produces: модуль `probe` со следующими именами, доступными как `probe.<name>` и реэкспортированными пакетом —
  `jar_path(jar_uri: str) -> str | None`, `jar_available(jar_uri: str, path: str) -> bool`,
  `_probe(path: str) -> bool`, `_probe_worker(path: str, slot: list[tuple[str, object]]) -> None`,
  `_query_endpoint(endpoint: str, path: str) -> str`, `_is_standby(error: HTTPError) -> bool`,
  `resolve_webhdfs_urls`, `_jar_memo: dict[str, tuple[bool, float]]`, `_now`,
  `_PROBE_DEADLINE_SEC: float`, `ENDPOINT_TIMEOUT_SEC: float`, `_MEMO_TTL_SEC: float`.

- [ ] **Step 1: Зафиксировать исходное состояние**

Run: `cd airflow/config && python -m pytest tests/ -q --tb=no`
Expected: `228 passed`. Если число другое — остановиться и сообщить: план построен на этом базисе.

- [ ] **Step 2: Создать `probe.py` и перенести код**

Создать `airflow/config/ol_policy/probe.py` с шапкой:

```python
"""Зонд openlineage-spark jar в HDFS: WebHDFS-опрос под дедлайном, мемо по URI.

Единственное место пакета, которое ходит в сеть. Вызывается только на рендере —
на парсе сетевой вызов съел бы бюджет разбора DAG-файла.
"""

from __future__ import annotations

import json
import threading
from urllib.error import HTTPError
from urllib.parse import quote, urlparse
from urllib.request import urlopen

from . import handlers
from . import hadoop_conf
from . import logger
from . import utils

# Ограничители зонда: дедлайн на весь перебор, таймаут одного эндпоинта, TTL мемо.
# Модульные, потому что тесты подменяют их monkeypatch'ем.
_PROBE_DEADLINE_SEC = 5.0
ENDPOINT_TIMEOUT_SEC = 2.0
_MEMO_TTL_SEC = 300.0

# Ре-экспорт time-источника для тестов: monkeypatch.setattr(probe, "_now", ...)
# должен попасть в нужный символ, а не в ``utils.now``.
_now = utils.now

# Мемо зонда: jar_uri -> (available, timestamp). Время — по ``_now``.
_jar_memo: dict[str, tuple[bool, float]] = {}

# Ре-экспорт резолвера эндпоинтов, чтобы тесты могли подменять его через
# ``monkeypatch.setattr(probe, "resolve_webhdfs_urls", ...)``.
resolve_webhdfs_urls = hadoop_conf.resolve_webhdfs_urls
```

Перенести из `__init__.py` **дословно, без правок тела**: `jar_path` (строки 495-509), `_is_standby` (511-523), `_query_endpoint` (525-544), `_probe` (546-575), `_probe_worker` (580-591), `jar_available` (593-638). Порядок в новом файле: `jar_path`, `_is_standby`, `_query_endpoint`, `_probe`, `_probe_worker`, `jar_available`.

- [ ] **Step 3: Заменить определения в `__init__.py` импортом**

Удалить из `__init__.py` перенесённые функции и константы (`_PROBE_DEADLINE_SEC`, `ENDPOINT_TIMEOUT_SEC`, `_MEMO_TTL_SEC`, `_now`, `_jar_memo`, `resolve_webhdfs_urls` и шесть функций). Вместо них — реэкспорт рядом с прочими:

```python
from . import probe
from .probe import ENDPOINT_TIMEOUT_SEC, jar_available, jar_path, resolve_webhdfs_urls
```

Вызовы внутри `_jar_ok` и `_resolve_jar`, которые сейчас зовут `jar_available(...)`, заменить на `probe.jar_available(...)` — обращение через модуль обязательно, иначе патч `probe.jar_available` не будет виден.

`reset_state` вместо `_jar_memo.clear()` вызывает `probe._jar_memo.clear()`.

Удалить из шапки `__init__.py` ставшие ненужными импорты: `threading`, `quote`, `urlparse`, `urlopen`, `HTTPError`, `handlers`, `hadoop_conf` — но только те, что больше нигде в файле не используются. Проверить командой: `grep -n "threading\.\|quote(\|urlparse(\|urlopen(\|HTTPError\|handlers\.\|hadoop_conf\." airflow/config/ol_policy/__init__.py`.

- [ ] **Step 4: Перевести 27 целей патчей на `probe`**

В `airflow/config/tests/test_ol_policy.py` заменить по строкам:

| Цель | Строки | Замена |
| --- | --- | --- |
| `jar_available` | 141, 156, 758, 1085, 1502, 1512, 1522, 1532, 1543, 1559, 1578, 1588, 1604, 1647, 1686, 2175, 2223, 2245 | `monkeypatch.setattr(ol_policy.probe, "jar_available", …)` |
| `resolve_webhdfs_urls` | 168, 867, 952 | `monkeypatch.setattr(ol_policy.probe, "resolve_webhdfs_urls", …)` |
| `urlopen` | 190, 759 | `monkeypatch.setattr(ol_policy.probe, "urlopen", …)` |
| `_PROBE_DEADLINE_SEC` | 905, 929 | `monkeypatch.setattr(ol_policy.probe, "_PROBE_DEADLINE_SEC", …)` |
| `_now` | 204 | `monkeypatch.setattr(ol_policy.probe, "_now", …)` |
| `_probe` | 1709 | `monkeypatch.setattr(ol_policy.probe, "_probe", …)` |

Номера строк — на момент написания плана; перед правкой найти каждую цель заново:
`grep -n 'setattr(ol_policy, "jar_available"\|setattr(ol_policy, "resolve_webhdfs_urls"\|setattr(ol_policy, "urlopen"\|setattr(ol_policy, "_PROBE_DEADLINE_SEC"\|setattr(ol_policy, "_now"\|setattr(ol_policy, "_probe"' airflow/config/tests/test_ol_policy.py`
Счёт совпадений обязан быть **27**. Если больше — правятся все; план построен на этом счёте, и расхождение означает, что счёт надо пересобрать целиком.

- [ ] **Step 5: Прогнать набор**

Run: `cd airflow/config && python -m pytest tests/ -q --tb=short`
Expected: `228 passed`.

- [ ] **Step 6: Доказать, что патчи действительно бьют в цель**

Временно сломать зонд и убедиться, что тесты это видят:

Run: `cd airflow/config && python -c "
import sys; sys.path.insert(0, '.')
import ol_policy
print('probe module:', ol_policy.probe.__name__)
print('facade sees same object:', ol_policy.jar_available is ol_policy.probe.jar_available)
"`
Expected: `probe module: ol_policy.probe` и `facade sees same object: True`.

- [ ] **Step 7: Commit**

```bash
git add airflow/config/ol_policy/probe.py airflow/config/ol_policy/__init__.py airflow/config/tests/test_ol_policy.py
git commit -m "refactor(ol_policy): move the HDFS jar probe into probe.py"
```

---

## Task 2: Вынести чтение Variable в `variable.py`

**Files:**
- Create: `airflow/config/ol_policy/variable.py`
- Modify: `airflow/config/ol_policy/__init__.py:40, 209-306`
- Modify: `airflow/config/tests/test_ol_policy.py` (1 цель патча)

**Interfaces:**
- Consumes: `logger.warn_once`.
- Produces: модуль `variable` с `VARIABLE: str`, `_clean(value: object, *, require_scheme: bool = False) -> str`,
  `_cfg() -> dict[str, object] | None` (под `functools.lru_cache(maxsize=1)`),
  `_validate_cfg() -> dict[str, object] | None` (под `functools.lru_cache(maxsize=1)`).

- [ ] **Step 1: Создать `variable.py` и перенести код**

Создать `airflow/config/ol_policy/variable.py` с шапкой:

```python
"""Чтение и проверка Airflow Variable ``openlineage_config``.

Читается только на рендере, на воркере: на парсе обращение к метастору съело бы
бюджет разбора DAG-файла. Никогда не бросает — при любой ошибке возвращает None,
и лайнидж просто не включается.
"""

from __future__ import annotations

import functools
import json

from . import logger

VARIABLE = "openlineage_config"
```

Перенести дословно: `_clean` (строки 209-222), `_cfg` (224-271 вместе с декоратором), `_validate_cfg` (274-306 вместе с декоратором).

- [ ] **Step 2: Заменить определения в `__init__.py` импортом**

Удалить перенесённое, добавить:

```python
from . import variable
from .variable import VARIABLE
```

Вызовы `_cfg()` / `_validate_cfg()` / `_clean(...)` в оставшемся коде `__init__.py` (внутри `ol_macro`, `_jar_ok`, `_scalar`) заменить на `variable._cfg()`, `variable._validate_cfg()`, `variable._clean(...)`.

`reset_state` вместо `_cfg.cache_clear()` / `_validate_cfg.cache_clear()` вызывает `variable._cfg.cache_clear()` / `variable._validate_cfg.cache_clear()`.

Удалить из шапки `__init__.py` импорты `functools` и `json`, если после правки они больше не используются — проверить: `grep -n "functools\.\|json\." airflow/config/ol_policy/__init__.py`.

- [ ] **Step 3: Перевести цель патча на `variable`**

Строка 1084 (найти заново: `grep -n 'setattr(ol_policy, "_validate_cfg"' airflow/config/tests/test_ol_policy.py`, ожидается **1** совпадение):

```python
monkeypatch.setattr(ol_policy.variable, "_validate_cfg", _counted)
```

В том же тесте `original = ol_policy._validate_cfg` заменить на `original = ol_policy.variable._validate_cfg` — иначе счётчик обернёт объект, который уже не тот, что зовёт `ol_macro`.

- [ ] **Step 4: Прогнать набор**

Run: `cd airflow/config && python -m pytest tests/ -q --tb=short`
Expected: `228 passed`.

- [ ] **Step 5: Commit**

```bash
git add airflow/config/ol_policy/variable.py airflow/config/ol_policy/__init__.py airflow/config/tests/test_ol_policy.py
git commit -m "refactor(ol_policy): move Variable reading and validation into variable.py"
```

---

## Task 3: Вынести совместимость оператора в `operator.py`

**Files:**
- Create: `airflow/config/ol_policy/operator.py`
- Modify: `airflow/config/ol_policy/__init__.py:60-207`
- Modify: `airflow/config/tests/test_ol_policy.py` (2 цели патчей)

**Interfaces:**
- Consumes: `logger.warn_once`, `utils.dag_and_task_ids`, `utils.task_dag`.
- Produces: модуль `operator` с `passthrough_exceptions() -> Tuple[Type[BaseException], ...]`,
  `operator_attrs(task: object) -> SimpleNamespace | None`, `_spark_submit_operator() -> type | None`,
  `_looks_like_spark_submit(task: object, operator_cls: type) -> bool`,
  `_level_forced(owner: object, level: str, dag_id: str, task_id: str) -> bool | None`,
  `lineage_forced(task: object) -> bool | None`, `_ATTR_CANDIDATES`, `_PASSTHROUGH_NAMES`,
  `_passthrough_cache: tuple[type[BaseException], ...] | None`.

- [ ] **Step 1: Создать `operator.py` и перенести код**

Создать `airflow/config/ol_policy/operator.py` с шапкой:

```python
"""Совместимость с двумя раскладками ``SparkSubmitOperator`` и тумблер из ``params``.

Провайдер 4.1.1 держит conf и jars приватными, 4.10.0 — публичными, поэтому имена
атрибутов резолвятся, а не зашиваются. Здесь же лесенка форса ``task.params`` →
``dag.params`` и список исключений, которые политика обязана пропускать наружу.
"""

from __future__ import annotations

import importlib
from types import SimpleNamespace
from typing import Tuple, Type

from . import logger
from . import utils

_PASSTHROUGH_NAMES = ("AirflowTaskTimeout", "AirflowClusterPolicyViolation", "AirflowClusterPolicySkipDag")
_passthrough_cache: tuple[type[BaseException], ...] | None = None

_ATTR_CANDIDATES: dict[str, tuple[str, ...]] = {"conf": ("conf", "_conf"), "jars": ("jars", "_jars")}
```

Перенести дословно: `passthrough_exceptions` (75-99), `operator_attrs` (101-123), `_spark_submit_operator` (125-135), `_looks_like_spark_submit` (137-157), `_level_forced` (159-188), `lineage_forced` (190-207).

- [ ] **Step 2: Заменить определения в `__init__.py` импортом**

```python
from . import operator
from .operator import lineage_forced, operator_attrs, passthrough_exceptions
```

Вызовы внутри оставшегося кода `__init__.py` (`apply_policy` зовёт `_spark_submit_operator`, `_looks_like_spark_submit`, `passthrough_exceptions`; `inject_openlineage` зовёт `operator_attrs`, `lineage_forced`) перевести на `operator.<name>(...)`.

`reset_state` сбрасывает кэш через модуль:

```python
    operator._passthrough_cache = None
```

`global _passthrough_cache` из `reset_state` убрать — переменная теперь живёт в `operator`.

Удалить из шапки `__init__.py` импорты `importlib`, `SimpleNamespace`, `Tuple`, `Type`, если они больше не используются — проверить: `grep -n "importlib\|SimpleNamespace\|Tuple\|Type" airflow/config/ol_policy/__init__.py`.

- [ ] **Step 3: Перевести 2 цели патчей на `operator`**

Строки 1731 и 1805 (найти заново: `grep -n 'setattr(ol_policy, "_spark_submit_operator"' airflow/config/tests/test_ol_policy.py`, ожидается **2** совпадения):

```python
monkeypatch.setattr(ol_policy.operator, "_spark_submit_operator", lambda: FakeOperator)
```

- [ ] **Step 4: Прогнать набор**

Run: `cd airflow/config && python -m pytest tests/ -q --tb=short`
Expected: `228 passed`.

- [ ] **Step 5: Commit**

```bash
git add airflow/config/ol_policy/operator.py airflow/config/ol_policy/__init__.py airflow/config/tests/test_ol_policy.py
git commit -m "refactor(ol_policy): move provider-layout compatibility into operator.py"
```

---

## Task 4: Вынести рендер-макрос в `render.py`

**Files:**
- Create: `airflow/config/ol_policy/render.py`
- Modify: `airflow/config/ol_policy/__init__.py:308-493`

**Interfaces:**
- Consumes: `variable._cfg`, `variable._validate_cfg`, `variable._clean`, `probe.jar_path`, `probe.jar_available`,
  `logger.warn_once`, `logger.logger`, `utils.merge_jars`, `utils.merge_listeners`.
- Produces: модуль `render` с `_refusal(dag_cur: str | None) -> str`,
  `_emit(value: str, dag_cur: str | None, merge: Callable[[object, object], str], key: str) -> str`,
  `_merge_jars_pair(dag_cur: object, our_jar: object) -> str`,
  `_scalar(value: str, dag_cur: str | None, key: str) -> str`,
  `_jar_ok(cfg: dict[str, object], *, log: bool = False) -> bool`,
  `_resolve_jar(cfg: dict[str, object], dag_cur: str | None) -> str`,
  `ol_macro(field: str, forced: bool | None = None, dag_cur: str | None = "") -> str`.

- [ ] **Step 1: Создать `render.py` и перенести код**

Создать `airflow/config/ol_policy/render.py` с шапкой:

```python
"""Рендер-фаза: Jinja зовёт ``ol_macro`` на воркере и получает значения лайниджа.

Здесь и только здесь читается Variable и проверяется наличие jar'а в HDFS.
Отказ по любой причине возвращает то, что задал сам DAG, а не пустую строку —
иначе выключенный лайнидж стирал бы чужие listener'ы и jar'ы.
"""

from __future__ import annotations

from typing import Callable

from . import logger
from . import probe
from . import utils
from . import variable
from .logger import logger as _logger
```

Перенести дословно: `_refusal` (308-321), `_emit` (323-345), `_merge_jars_pair` (347-358), `_scalar` (360-382), `_jar_ok` (384-418), `_resolve_jar` (420-437), `ol_macro` (439-493).

Внутри перенесённого кода заменить обращения на модульные: `_cfg()` → `variable._cfg()`, `_validate_cfg()` → `variable._validate_cfg()`, `_clean(...)` → `variable._clean(...)`, `jar_path(...)` → `probe.jar_path(...)`, `jar_available(...)` → `probe.jar_available(...)`, `merge_listeners` → `utils.merge_listeners`.

- [ ] **Step 2: Заменить определения в `__init__.py` импортом**

```python
from . import render
from .render import ol_macro
```

`inject_openlineage` в `parse`-части кладёт в `dag.user_defined_macros` именно `ol_macro` — после переезда это `render.ol_macro`. Проверить, что объект тот же: реэкспорт `from .render import ol_macro` даёт ту же функцию, поэтому сравнение `macros[MACRO] is not ol_macro` продолжает работать.

- [ ] **Step 3: Прогнать набор**

Run: `cd airflow/config && python -m pytest tests/ -q --tb=short`
Expected: `228 passed`.

- [ ] **Step 4: Commit**

```bash
git add airflow/config/ol_policy/render.py airflow/config/ol_policy/__init__.py
git commit -m "refactor(ol_policy): move the render-time macro into render.py"
```

---

## Task 5: Вынести сборку строк в `parse.py`

**Files:**
- Create: `airflow/config/ol_policy/parse.py`
- Modify: `airflow/config/ol_policy/__init__.py:39, 46, 641-743`
- Modify: `airflow/config/tests/test_ol_policy.py` (2 цели патчей)

**Interfaces:**
- Consumes: `operator.operator_attrs`, `operator.lineage_forced`, `logger.warn_once`, `utils.task_dag`,
  `utils.dag_and_task_ids`, `utils.merge_jars`, и `ol_macro` — **передаётся аргументом, не импортируется**
  (см. Step 2).
- Produces: модуль `parse` с `MACRO: str`, `_UNSAFE_FOR_LITERAL: tuple[str, ...]`,
  `_dag_channel(value: object) -> tuple[str, str | None]`,
  `_macro_call(field: str, forced: str, dag_cur: str | None) -> str`,
  `inject_openlineage(task: object) -> None`.

- [ ] **Step 1: Создать `parse.py` и перенести код**

Создать `airflow/config/ol_policy/parse.py` с шапкой:

```python
"""Парс-фаза: политика собирает строки и регистрирует макрос, ничего не резолвя.

Ноль обращений к Variable, метастору и HDFS: парс идёт внутри DAG-file-processor'а
шедулера, где зависший вызов съедает бюджет разбора всего файла. Значения приезжают
на рендере — см. ``render``.
"""

from __future__ import annotations

from . import logger
from . import operator
from . import utils

MACRO = "__openlineage_v1"

# Значение с этими фрагментами нельзя вложить литералом в текст вызова макроса:
# Jinja порвётся на вложенных скобках, кавычка — на самой кавычке, а обратный
# слэш Jinja развернёт как escape внутри строкового литерала ("C:\new.jar"
# приедет как "C:" + перевод строки + "ew.jar").
_UNSAFE_FOR_LITERAL = ("{{", "{%", "'", '"', "\\")
```

Перенести дословно: `_dag_channel` (641-657), `_macro_call` (659-669), `inject_openlineage` (671-743).

Внутри `inject_openlineage` заменить `operator_attrs(task)` → `operator.operator_attrs(task)`, `lineage_forced(task)` → `operator.lineage_forced(task)`.

- [ ] **Step 2: Разорвать зависимость от `render`**

`inject_openlineage` кладёт в `dag.user_defined_macros` объект `ol_macro`. Импортировать `render` в `parse` нельзя — это связало бы фазы, ради разделения которых всё и делается, и открыло бы `parse` дорогу к `variable`/`probe` через транзитивный импорт.

Решение: макрос передаётся параметром со значением по умолчанию, которое подставляет фасад.

```python
def inject_openlineage(task: object, macro: object = None) -> None:
    """Навешивает OpenLineage на проверенную Spark-таску: макрос и строки в conf.

    :param task: экземпляр ``SparkSubmitOperator``; мутируется на месте.
    :param macro: рендер-функция, которую кладём в ``dag.user_defined_macros``.
        ``None`` — взять из фасада пакета; параметр существует, чтобы ``parse``
        не импортировал ``render`` и не тянул за собой Variable и зонд.
    :return: None.
    """
    if macro is None:
        from . import render
        macro = render.ol_macro
```

Ленивый импорт внутри функции — не нарушение ограничения: на уровне модуля `parse` по-прежнему не знает ни про `render`, ни про `variable`, ни про `probe`, а вызывается он уже после того, как пакет собран.

Дальше по телу: `macros[MACRO] is not ol_macro` → `macros[MACRO] is not macro`, и `macros[MACRO] = ol_macro` → `macros[MACRO] = macro`.

- [ ] **Step 3: Заменить определения в `__init__.py` импортом**

```python
from . import parse
from .parse import MACRO, inject_openlineage
```

- [ ] **Step 4: Перевести 2 цели патчей на `parse`**

Строки 1884 и 1926 (найти заново: `grep -n 'setattr(ol_policy, "inject_openlineage"' airflow/config/tests/test_ol_policy.py`, ожидается **2** совпадения). Эти тесты подменяют инъекцию, чтобы проверить `apply_policy`, а `apply_policy` живёт в `__init__.py` и зовёт её. Поэтому цель зависит от того, как фасад её вызывает: после Task 6 `apply_policy` зовёт `parse.inject_openlineage(task)`, значит патчить надо `ol_policy.parse`:

```python
monkeypatch.setattr(ol_policy.parse, "inject_openlineage", _record)
```

- [ ] **Step 5: Прогнать набор**

Run: `cd airflow/config && python -m pytest tests/ -q --tb=short`
Expected: `228 passed`.

- [ ] **Step 6: Проверить несущее ограничение**

Run: `cd airflow/config && grep -n "^from \.\|^from \. import\|^import " ol_policy/parse.py`
Expected: в выводе есть `logger`, `operator`, `utils` и нет ни `variable`, ни `probe`, ни `render`.

- [ ] **Step 7: Commit**

```bash
git add airflow/config/ol_policy/parse.py airflow/config/ol_policy/__init__.py airflow/config/tests/test_ol_policy.py
git commit -m "refactor(ol_policy): move parse-time string assembly into parse.py"
```

---

## Task 6: Свести `__init__.py` к фасаду и закрыть три минора

**Files:**
- Modify: `airflow/config/ol_policy/__init__.py` (целиком)
- Modify: `airflow/config/ol_policy/parse.py` (аннотация `_macro_call`)
- Modify: `airflow/config/ol_policy/render.py`, `probe.py`, `variable.py`, `operator.py` (импорт логгера)

**Interfaces:**
- Consumes: всё, что произвели Tasks 1-5.
- Produces: `apply_policy(task: object) -> None`, `reset_state() -> None` и реэкспорты — публичный контракт пакета, неизменный для `airflow_local_settings.py` и тестов.

- [ ] **Step 1: Переписать `__init__.py` целиком**

```python
"""Cluster policy стенда: инъекция OpenLineage в Spark-таски Airflow.

OL-листенер вынесен из общего ``spark-defaults.conf`` (он ломал интерактивный
``spark-shell``), поэтому Airflow навешивает лайнидж своим ``SparkSubmitOperator``
сам — без правок в DAG'ах.

Пакет разложен по фазам жизненного цикла политики: ``parse`` собирает строки на
разборе DAG-файла, ``render`` резолвит значения на воркере, ``variable`` читает
Airflow Variable, ``probe`` ходит в HDFS, ``operator`` знает про две раскладки
провайдера. Здесь остаётся только точка входа и общий сброс состояния.

Политика ничего не роняет: любая ошибка гасится и превращается в «лайниджа нет».
Ни один модуль пакета не импортирует Airflow на уровне модуля — импорт идёт внутри
функций, поэтому набор тестов запускается без установленного Airflow.
"""

from __future__ import annotations

from . import logger
from . import operator
from . import parse
from . import probe
from . import render
from . import utils
from . import variable
from .operator import lineage_forced, operator_attrs, passthrough_exceptions
from .parse import MACRO, inject_openlineage
from .probe import ENDPOINT_TIMEOUT_SEC, jar_available, jar_path, resolve_webhdfs_urls
from .render import ol_macro
from .variable import VARIABLE


def apply_policy(task: object) -> None:
    """Точка входа cluster policy: гейт типа таски и общий перехват ошибок.

    Любая ошибка политики гасится: исключение отсюда роняет импорт всего
    DAG-файла, то есть баг выключил бы все DAG'и разом. Чужой механизм таймаута
    и чужое решение пропустить DAG пробрасываются наружу.

    :param task: любая таска Airflow; мутируется на месте на этапе парсинга.
    :return: None.
    """
    try:
        operator_cls = operator._spark_submit_operator()
        if operator_cls is None:
            return
        if not isinstance(task, operator_cls):
            if operator._looks_like_spark_submit(task, operator_cls):
                dag_id, task_id = utils.dag_and_task_ids(task)
                logger.warn_once(("mapped", dag_id, task_id), "OpenLineage не включён: динамический маппинг тасок не поддерживается (%s.%s)", dag_id, task_id)
            return
        parse.inject_openlineage(task)
    except operator.passthrough_exceptions():
        raise
    except Exception:
        dag_id, task_id = utils.dag_and_task_ids(task)
        logger.warn_once(("unexpected", dag_id, task_id), "OpenLineage не включён: непредвиденная ошибка cluster policy (%s.%s)", dag_id, task_id, exc_info=True)


def reset_state() -> None:
    """Сбрасывает всё модульное состояние политики.

    Зовётся фикстурой ``_reset_policy_state`` (conftest.py) до и после каждого
    теста: дедупликация warning'ов, кэши конфига, мемо зонда и кэш классов
    исключений переживают границу теста и без сброса смешали бы результаты.
    Единственное место, которое знает обо всех четырёх хранилищах сразу.

    :return: None.
    """
    logger._warned.clear()
    variable._cfg.cache_clear()
    variable._validate_cfg.cache_clear()
    probe._jar_memo.clear()
    operator._passthrough_cache = None


# Реэкспорт утилит: тесты и вызывающий код обращаются к ним через пакет политики.
merge_jars = utils.merge_jars
merge_listeners = utils.merge_listeners
```

- [ ] **Step 2: Починить ловушку имён логгера**

В `render.py` строка `from .logger import logger as _logger` создаёт пару `logger` (модуль) / `_logger` (объект). `logger.info(...)` был бы `AttributeError` в рантайме. Заменить в `render.py` на явные имена:

```python
from .logger import logger as log
from .logger import warn_once
```

и по телу: `_logger.info(...)` → `log.info(...)`, `logger.warn_once(...)` → `warn_once(...)`. То же проделать в `probe.py` и `variable.py`, где используется только `warn_once`: `from .logger import warn_once`, и по телу `logger.warn_once(...)` → `warn_once(...)`. В `operator.py` и `parse.py` — так же.

В `__init__.py` остаётся `from . import logger` ради `logger._warned.clear()` в `reset_state`.

- [ ] **Step 3: Ужать тип `forced` в `_macro_call`**

В `parse.py`:

```python
from typing import Literal


def _macro_call(field: str, forced: Literal["true", "none"], dag_cur: str | None) -> str:
```

Тело не меняется. Значение уходит в текст Jinja-вызова неэкранированным, поэтому Python-овские `True`/`False` отрендерились бы невалидным идентификатором — тип это запрещает на уровне проверки.

- [ ] **Step 4: Удалить доказанно недостижимые ветки**

Кандидаты, названные ревью цикла 1:
- `render._scalar`: `if not value:` — `variable._validate_cfg` уже отвергает пустые `url`/`namespace`.
- `render._jar_ok`: `if not jar_uri:` — та же причина, пустой `openlineage_jar` не проходит валидацию.

Для **каждого** кандидата доказать недостижимость до удаления:

Run: `cd airflow/config && python -m pytest tests/ -q -k "scalar or jar_ok or bad_field or jar_unset" --tb=short`

Затем временно заменить тело ветки на `raise AssertionError("unreachable")` и прогнать весь набор:

Run: `cd airflow/config && python -m pytest tests/ -q --tb=line`
Expected: `228 passed` — ни один тест в ветку не попал.

Только после зелёного прогона удалить ветку вместе с её `warn_once`. Если хоть один тест упал — ветка достижима, **оставить её как есть** и записать это в отчёт. Лишний `if` дешевле регрессии.

- [ ] **Step 5: Прогнать набор и проверить размеры**

Run: `cd airflow/config && python -m pytest tests/ -q --tb=short`
Expected: `228 passed`.

Run: `cd airflow/config && wc -l ol_policy/*.py`
Expected: `__init__.py` не длиннее ~120 строк; ни один модуль не длиннее ~260.

- [ ] **Step 6: Commit**

```bash
git add airflow/config/ol_policy/
git commit -m "refactor(ol_policy): reduce __init__.py to the package facade"
```

---

## Task 7: Контрольная проверка инвариантов

**Files:**
- Modify: none — задача только проверяет.

**Interfaces:**
- Consumes: итоговое состояние пакета после Tasks 1-6.
- Produces: ничего; её результат — отчёт.

- [ ] **Step 1: Ни один модуль не импортирует Airflow на уровне модуля**

Run: `cd airflow/config && grep -n "^import airflow\|^from airflow" ol_policy/*.py`
Expected: пустой вывод. Импорты Airflow допустимы только внутри функций — проверить, что найденные `grep -rn "from airflow" ol_policy/` строки имеют отступ.

- [ ] **Step 2: Парс не тянет рендер-фазу**

Run: `cd airflow/config && grep -n "^from \.\|^import " ol_policy/parse.py`
Expected: нет `variable`, `probe`, `render` на уровне модуля.

- [ ] **Step 3: Не осталось патчей, бьющих мимо цели**

Run: `grep -c 'setattr(ol_policy, "' airflow/config/tests/test_ol_policy.py`
Expected: `0`. Каждый патч теперь адресует модуль: `setattr(ol_policy.probe, …)` и подобные.

Run: `grep -c 'setattr(ol_policy\.' airflow/config/tests/test_ol_policy.py`
Expected: `32` — столько же целей, сколько было до разбиения.

- [ ] **Step 4: Патчи действительно перехватывают вызовы**

Убедиться, что подмена зонда влияет на макрос — если бы патч бил мимо, тест остался бы зелёным при сломанном зонде:

Run: `cd airflow/config && python -c "
import sys, json, types; sys.path.insert(0, '.')
import ol_policy
raw = json.dumps({'enabled': True, 'spark_conf': {'spark.extraListeners': 'io.ol.L', 'spark.openlineage.transport.url': 'http://m:5000', 'spark.openlineage.namespace': 'ns'}, 'openlineage_jar': 'hdfs://n:9000/o.jar'})
class V:
    @staticmethod
    def get(k, default_var=None, **kw): return raw
m = types.ModuleType('airflow.models'); m.Variable = V
p = types.ModuleType('airflow'); p.models = m
sys.modules['airflow'] = p; sys.modules['airflow.models'] = m
ol_policy.reset_state()
ol_policy.probe.jar_available = lambda uri, path: True
print('probe True  ->', repr(ol_policy.ol_macro('listener', None, '')))
ol_policy.reset_state()
ol_policy.probe.jar_available = lambda uri, path: False
print('probe False ->', repr(ol_policy.ol_macro('listener', None, '')))
"`
Expected: `probe True  -> 'io.ol.L'` и `probe False -> ''`. Разные ответы доказывают, что подмена на модуле долетает до `render`.

- [ ] **Step 5: Полный прогон и сверка с базисом**

Run: `cd airflow/config && python -m pytest tests/ -q --tb=short`
Expected: `228 passed` — ровно столько же, сколько до разбиения.

- [ ] **Step 6: Диф не содержит логики**

Run: `git diff 757db5c..HEAD -- airflow/config/ol_policy/ | grep "^[+-]" | grep -v "^[+-][+-]" | grep -c ""`
Записать число в отчёт. Затем просмотреть диф глазами и подтвердить: перемещения, правка импортов, `Literal`, удаление доказанно недостижимых веток. Ни одного нового `if`, ни одного изменённого текста лога.

---

## Self-Review

**1. Покрытие спеки.**

| Раздел спеки | Задача |
| --- | --- |
| §3 структура: `probe.py` | Task 1 |
| §3 структура: `variable.py` | Task 2 |
| §3 структура: `operator.py` | Task 3 |
| §3 структура: `render.py` | Task 4 |
| §3 структура: `parse.py` | Task 5 |
| §3 несущее ограничение (парс не тянет variable/probe) | Task 5 Step 6, Task 7 Step 2 |
| §4 контракт фасада, три группы реэкспортов | Task 6 Step 1 |
| §4 `reset_state` знает обо всём состоянии | Task 6 Step 1 |
| §5 ловушка имён логгера | Task 6 Step 2 |
| §5 `Literal["true", "none"]` | Task 6 Step 3 |
| §5 недостижимые ветки только с доказательством | Task 6 Step 4 |
| §6 перевод 32 целей патчей | Tasks 1, 2, 3, 5 (27 + 1 + 2 + 2) |
| §7 критерий приёмки | Task 6 Step 5, Task 7 |
| §8 риск циклических импортов | Task 5 Step 2 (ленивый импорт), Task 7 Step 2 |
| §8 риск `resolve_webhdfs_urls` остаётся реэкспортом | Task 1 Step 2 |
| §8 риск пропущенной цели патча | Task 7 Steps 3-4 |

**2. Плейсхолдеры.** Каждый шаг несёт либо готовый код, либо точную команду с ожидаемым выводом. Формулировок «перенести аналогично», «добавить обработку ошибок», «написать тесты для вышеописанного» нет: номера строк исходника указаны для каждого переносимого символа, и каждый шаг, опирающийся на счёт совпадений, требует пересчитать его заново перед правкой.

**3. Согласованность типов.**

- `_dag_channel(value: object) -> tuple[str, str | None]` — объявлена в Task 5, потребляется `inject_openlineage` там же.
- `_macro_call(field: str, forced: Literal["true", "none"], dag_cur: str | None) -> str` — Task 5 переносит, Task 6 Step 3 ужимает тип. Оба места согласованы: до Task 6 сигнатура остаётся `forced: str`.
- `inject_openlineage(task: object, macro: object = None) -> None` — новый второй параметр вводится в Task 5 Step 2 и нигде больше не меняется; `apply_policy` в Task 6 зовёт её одним аргументом.
- `_resolve_jar(cfg: dict[str, object], dag_cur: str | None) -> str` — Task 4, сигнатура не меняется.
- `_jar_ok(cfg: dict[str, object], *, warn: bool = False) -> bool` — Task 4 переносит её как `log: bool`, Task 6 переименовывает ключевой параметр в `warn`. Причина вскрылась при исполнении: Task 6 импортирует объект логгера как `log`, и параметр с тем же именем затенил бы его — `log.warning(...)` стал бы вызовом на `bool`, то есть `AttributeError` на единственной ветке, которая называет причину отказа по jar'у. Оба вызова обновлены вместе с сигнатурой.
- `ol_macro(field: str, forced: bool | None = None, dag_cur: str | None = "") -> str` — Task 4, сигнатура не меняется; фасад реэкспортирует тот же объект.

**4. Каверзы, отмеченные в задачах.**

- Номера строк в таблицах патчей верны на момент написания плана и сдвигаются после каждой правки — каждый шаг требует найти цели заново своим `grep` и сверить счёт.
- `parse` не может импортировать `render` на уровне модуля, поэтому макрос приходит параметром с ленивым импортом внутри функции (Task 5 Step 2). Это единственное место в плане, где вводится новый параметр, и он не меняет поведения: значение по умолчанию даёт ровно прежний объект.
- Тест `test_validate_cfg_runs_once_per_process` берёт `original = ol_policy._validate_cfg` и оборачивает его счётчиком — если перевести только `setattr`, а не `original`, счётчик обернёт не тот объект (Task 2 Step 3).
