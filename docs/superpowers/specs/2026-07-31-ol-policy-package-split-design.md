# Разбиение пакета `ol_policy` по фазам жизненного цикла

**Дата:** 2026-07-31
**Статус:** утверждён (brainstorming), готов к плану реализации
**Артефакт:** `airflow/config/ol_policy/`, `airflow/config/tests/test_ol_policy.py`
**Предшественник:** [цикл 1, Ревизия 3](2026-07-30-ol-policy-variable-sparkconf-design.md) — установил границу «парс собирает строки, рендер резолвит значения»

## 1. Контекст и цель

После цикла 1 `ol_policy/__init__.py` — **791 строка и 34 top-level символа**: константы зонда, совместимость
двух раскладок провайдера, тумблер из `params`, чтение Variable, четыре ветки макроса, HDFS-зонд с мемо,
сборка строк на парсе и точка входа. Все семь ответственностей лежат в одном файле, и граница парс/рендер —
главное достижение цикла 1 — существует только в головах и в тестах.

Цель одна: **сделать структуру файлов носителем инвариантов**, а не комментариев о них.

**Не входит в скоуп:** изменение поведения; изменение публичного контракта (`apply_policy`, `reset_state`);
переписывание тестовых утверждений; правка `utils.py`, `logger.py`, `hadoop_conf.py`, `handlers.py`.

## 2. Грундинг-бриф

| Факт | Источник | Следствие для разбиения |
| --- | --- | --- |
| `airflow_local_settings.py` должен лежать в `sys.path` либо в `$AIRFLOW_HOME/config`; Airflow добавляет в `sys.path` три каталога — dags, config, plugins | [howto/set-config](https://airflow.apache.org/docs/apache-airflow/stable/howto/set-config.html), [modules_management](https://airflow.apache.org/docs/apache-airflow/stable/administration-and-deployment/modules_management.html) (запрос `"airflow_local_settings.py location config directory sys.path cluster policy module imports"`, 2026-07-31) | каталог `config` целиком в `sys.path`, поэтому пакет с подмодулями резолвится; ограничений на число модулей нет |
| Cluster policy настраивается созданием `airflow_local_settings.py` в Python search path | [cluster-policies](https://airflow.apache.org/docs/apache-airflow/stable/administration-and-deployment/cluster-policies.html) | точка входа остаётся ровно там же; разбиение её не касается |
| `ol_policy` не импортирует Airflow на уровне модуля — набор тестов гоняется без установленного Airflow | инвариант предка, `conftest.py:21` кладёт `airflow/config` в `sys.path` | правило наследуется **каждым** новым модулем: `from airflow… ` только внутри функций |

## 3. Структура

| Файл | Содержимое | Импортирует из пакета |
| --- | --- | --- |
| `__init__.py` | `apply_policy`, `reset_state`, реэкспорты публичных и патчируемых имён | `parse`, `render`, `probe`, `variable`, `operator`, `utils` |
| `parse.py` | `inject_openlineage`, `_dag_channel`, `_macro_call`, `_UNSAFE_FOR_LITERAL`, `MACRO` | `operator`, `utils`, `logger`; `render` — лениво, импортом внутри `inject_openlineage` |
| `render.py` | `ol_macro`, `_emit`, `_refusal`, `_scalar`, `_jar_ok`, `_resolve_jar`, `_merge_jars_pair` | `variable`, `probe`, `utils`, `logger` |
| `variable.py` | `_cfg`, `_validate_cfg`, `_clean`, `VARIABLE` | `logger` |
| `probe.py` | `jar_path`, `jar_available`, `_probe`, `_probe_worker`, `_query_endpoint`, `_is_standby`, `_jar_memo`, `_now`, `resolve_webhdfs_urls`, `_PROBE_DEADLINE_SEC`, `ENDPOINT_TIMEOUT_SEC`, `_MEMO_TTL_SEC` | `hadoop_conf`, `handlers`, `logger`, `utils` |
| `operator.py` | `operator_attrs`, `_spark_submit_operator`, `_looks_like_spark_submit`, `_level_forced`, `lineage_forced`, `passthrough_exceptions`, `_ATTR_CANDIDATES`, `_PASSTHROUGH_NAMES` | `utils`, `logger` |
| `utils.py`, `logger.py`, `hadoop_conf.py`, `handlers.py` | без изменений | — |

**Несущее ограничение: `parse.py` не импортирует ни `variable`, ни `probe`.** Инварианты 6 и 7 предка («на
парсе ноль обращений к Variable, метастору и HDFS») перестают держаться на одних тестах: нарушить их теперь
можно только дописав импорт, который виден в шапке файла и в ревью. Это и есть смысл резать по фазе, а не по
слою.

Обратное направление тоже фиксируется: `render.py` не импортирует `parse`. Фазы не знают друг о друге; связывает
их только фасад.

## 4. Контракт `__init__.py`

Фасад реэкспортирует две группы имён, и каждая нужна по своей причине:

1. **Публичные** — `apply_policy`, `reset_state`. Их зовёт `airflow_local_settings.task_policy`.
2. **Читаемые пакетом-вызывающей стороной, но не патч-точки** — `jar_available`, `jar_path`,
   `inject_openlineage`, `ol_macro`, `operator_attrs`, `lineage_forced`, `passthrough_exceptions`, `MACRO`,
   `merge_jars`, `merge_listeners`. Это плоские рёбайндинги: код пакета их не читает, он всегда обращается к
   атрибуту через модуль-владелец. Реэкспорт сохраняет **чтение** через `ol_policy.X`; **патч** через
   `ol_policy.X` не влияет на внутренние вызовы (см. §6) — `monkeypatch.setattr` должен целиться в
   submodule (`ol_policy.probe.X`, `ol_policy.render.X` и т. д.).

`urlopen`, `_spark_submit_operator`, `_validate_cfg`, `_probe`, `_now`, `resolve_webhdfs_urls` фасадом не
реэкспортируются вовсе: тесты патчят их напрямую на submodule (`ol_policy.probe.urlopen`,
`ol_policy.operator._spark_submit_operator` и т. п.). `ENDPOINT_TIMEOUT_SEC`, `resolve_webhdfs_urls` и
`VARIABLE` не используются ни тестами, ни остальным кодом репозитория ни в форме `ol_policy.X`, ни как
голый импорт — фасад их не реэкспортирует; они остаются достижимы как `ol_policy.probe.*` /
`ol_policy.variable.*`.

`reset_state` остаётся в `__init__.py`: она единственная знает обо всём модульном состоянии сразу — `_warned`
в `logger`, кэши `_cfg`/`_validate_cfg` в `variable`, `_jar_memo` в `probe`, `_passthrough_cache` в `operator`.
Разложить её по модулям значило бы завести четыре частичных сброса и точку, где о них надо помнить.

## 5. Попутные исправления

Три минора из финального ревью цикла 1, все внутри перемещаемого кода:

- **Ловушка имён.** Модуль держит `logger` (сосед-модуль с `warn_once`) и `_logger` (объект `logging.Logger`).
  `logger.info(...)` — `AttributeError` в рантайме. При разносе импортировать явно: `from .logger import
  warn_once, logger as log`.
- **`_macro_call(forced: str)`** ужимается до `Literal["true", "none"]`: значение уходит в текст Jinja-вызова
  неэкранированным, и `True`/`False` из Python отрендерились бы невалидным идентификатором.
- **Недостижимые ветки** (`_scalar`'s `if not value`, `_resolve_jar`'s `if not jar_uri`, повторные
  `isinstance`-проверки, которые уже гарантировал `_cfg`) — удаляются **только с доказательством
  недостижимости**: тест, который бьёт в ветку, должен отсутствовать, а вызывающий код — отсекать случай выше.
  Без доказательства защитная проверка остаётся: лишний `if` дешевле регрессии.

## 6. Тесты

Меняются **только цели патчей**, ни одно утверждение:

```python
monkeypatch.setattr(ol_policy, "jar_available", ...)        # было
monkeypatch.setattr(ol_policy.probe, "jar_available", ...)  # стало
```

Причина механическая: `monkeypatch.setattr` подменяет атрибут конкретного объекта, а вызов внутри `render.py`
резолвится через `probe.jar_available` — то есть смотрит на модуль `probe`, а не на пакет. Патч пакета остался
бы невидимым, и тест молча проверял бы не то, что думает.

Сводка целей — **32 вызова**, пересчитано по полному списку `grep -n "setattr(ol_policy," `:

| Цель | Раз | Новый модуль |
| --- | --- | --- |
| `jar_available` | 18 | `probe` |
| `resolve_webhdfs_urls` | 3 | `probe` |
| `urlopen` | 2 | `probe` |
| `_PROBE_DEADLINE_SEC` | 2 | `probe` |
| `_probe` | 1 | `probe` |
| `_now` | 1 | `probe` |
| `_spark_submit_operator` | 2 | `operator` |
| `inject_openlineage` | 2 | `parse` |
| `_validate_cfg` | 1 | `variable` |

27 из 32 уезжают в `probe` — это и делает зонд первым кандидатом на вынос.

Обращения на **чтение** (`ol_policy.ol_macro(...)`, `ol_policy._dag_channel(...)` и прочие 166) продолжают
работать через реэкспорты и не правятся.

## 7. Критерий приёмки

- `cd airflow/config && python -m pytest tests/ -q` — **228 passed**, столько же, сколько до разбиения.
- `airflow/config/ol_policy/__init__.py` — не длиннее ~120 строк.
- `grep -n "^from\|^import" airflow/config/ol_policy/parse.py` не содержит ни `variable`, ни `probe`.
- Ни один `import airflow` на уровне модуля ни в одном файле пакета.
- `git diff` по логике пуст: перемещения, переименование импортов логгера, аннотация `Literal`, удаление
  доказанно недостижимых веток. Ни одного нового `if`, ни одного изменённого сообщения лога.

## 8. Риски

- **Циклические импорты.** `__init__.py` импортирует все модули; модуль, импортировавший пакет верхнего уровня,
  замкнёт цикл. Правило: подмодули импортируют **только соседей**, никогда `from . import` самого пакета.
- **Порядок инициализации.** `probe.resolve_webhdfs_urls` — реэкспорт `hadoop_conf.resolve_webhdfs_urls`,
  сделанный на уровне модуля; он должен остаться реэкспортом, иначе тесты, патчащие его, промахнутся.
- **Пропущенная цель патча.** Тест, чей патч не переехал, останется зелёным, но перестанет проверять
  заявленное. Защита: после разбиения прогнать набор с `-p no:randomly` и сверить число passed, затем точечно
  проверить, что тесты зонда падают при намеренно сломанном `probe.jar_available`.
