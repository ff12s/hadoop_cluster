# Чистка пакета `ol_policy`: типизация конфига, один мердж, живой набор тестов

Дата: 2026-07-31
Статус: утверждён

## Задача

Сделать `airflow/config/ol_policy` максимально читаемым и без дублей. Поведение
политики не меняется ни в одной ветке: это рефакторинг, а не правка логики.
Продолжение сплита по фазам жизненного цикла (`f23525a`), который разложил пакет
по модулям, но оставил внутри модулей дубли и нетипизированные контракты.

## Грунтинг

- Airflow **2.6.3** (`airflow/requirements.txt`, `airflow/Dockerfile`: базовый образ
  `apache/airflow:2.6.3-python3.10`), провайдер `apache-airflow-providers-apache-spark` **4.1.1**,
  Python **3.10**.
- `/apache/airflow/2_7_3`, запрос `"cluster policy task_policy in airflow_local_settings mutating task
  at parse time, allowed exceptions AirflowClusterPolicyViolation"` → `cluster-policies.rst`:
  «Mutate task in place» — политика мутирует таску на месте, что и делает `parse.inject_openlineage`;
  `AirflowClusterPolicySkipDag` появляется в 2.7, поэтому поимённая сборка кортежа
  в `operator.passthrough_exceptions` остаётся как есть.
- `/apache/airflow/2_7_3`, запрос `"user_defined_macros on DAG and rendering template_fields with Jinja
  on the worker, params access in templates"` → `fundamentals.rst`, `faq.rst`: `user_defined_macros`
  — вызываемые объекты, которые Jinja зовёт на рендере; макрос не рендерится внутри другого шаблона.
  Схема «строка с вызовом макроса на парсе, значение на рендере» подтверждена.
- Документация версии 2.6.3 в context7 отсутствует; ближайший снапшот — 2.7.3, дельта названа.
- `dict.fromkeys` как дедуп с сохранением порядка — гарантия языка (порядок вставки в dict, CPython 3.7+),
  а не изобретённый механизм.

## Блокер, снимаемый первым

`tests/test_ol_policy.py:2167` держит `pytest.importorskip("airflow.models.dag")` на уровне модуля.
Он скипает весь файл, а не пять сквозных тестов под собой: без установленного Airflow из 2278 строк
не выполняется ни один тест, зелёными остаются только 12 тестов `test_hadoop_conf.py`. Docstring файла
при этом обещает запуск голым `python -m pytest` без Airflow.

Пока это не снято, «поведение не изменилось» непроверяемо, поэтому задача идёт первой. Шаблон уже есть
в том же файле на строке 2005: `@pytest.mark.skipif(not _airflow_installed(), ...)` плюс локальный импорт
внутри теста.

## Правки

### 1. `variable.Config` — типизированный результат валидации

`_validate_cfg` уже проверяет ровно четыре поля и выбрасывает результат проверки, из-за чего `render`
разбирает тот же dict заново. Он начинает возвращать `NamedTuple`:

```python
class Config(NamedTuple):
    listener: str
    url: str
    namespace: str
    jar_uri: str
```

`_cfg` не меняется: он отдаёт сырой словарь, `ol_macro` читает по нему `enabled` до валидации, тесты
патчат именно его.

Следствия в `render.py`: уходят проверки `isinstance` на `spark_conf` и `openlineage_jar`, оба
независимых пересчёта `jar_uri` в `_jar_ok` и `_resolve_jar`, все повторные вызовы `_clean` на рендере.
Тождественность поведения: предикаты `_clean` на рендере и в `_validate_cfg` совпадают дословно
(включая `require_scheme=True` для url), поэтому пустое значение на рендере недостижимо и раньше.

### 2. `utils.merge_csv` — один мердж вместо трёх

`merge_jars`, `merge_listeners` и `render._merge_jars_pair` — одна функция, записанная трижды: дедуп
с сохранением порядка по CSV-источникам, где значение с Jinja не режется по запятой.

```python
def merge_csv(*sources: object) -> str:
    return ",".join(dict.fromkeys(item for source in sources for item in _csv_items(source)))
```

`_jar_items` переименовывается в `_csv_items`: его зовут и listener'ы. `render._merge_jars_pair`
удаляется, `_emit` получает `utils.merge_csv` напрямую в обеих ветках. `__all__` пакета отдаёт
`merge_csv` вместо двух прежних имён.

### 3. `probe` — типизированный слот вместо магических строк

Слот демон-потока `list[tuple[str, object]]` с метками `"ok"`/`"err"` становится
`list[bool | BaseException]`, разбор — по `isinstance`. `_query_endpoint` получает возвращаемый тип
`Literal["found", "absent", "standby", "error"]`. Демон-поток и дедлайн остаются: таймаут сокета
не покрывает `getaddrinfo`, а `ThreadPoolExecutor` join'ит воркеры на выходе интерпретатора.

### 4. `operator.OperatorAttrs`

`operator_attrs` возвращает `NamedTuple` с полями `conf` и `jars` вместо `SimpleNamespace` — call-sites
(`attrs.conf`, `attrs.jars`) не меняются, тип появляется. `typing.Tuple`/`typing.Type` заменяются
встроенными.

### 5. Стиль и комментарии

- Строки длиннее 120 символов переносятся (`__init__.py`, `operator.py`, `render.py`).
- `utils.py` и `logger.py` получают module-docstring, `from __future__ import annotations`
  и две пустые строки между определениями.
- Docstring'и ужимаются до «почему». Остаются: инвариант 19, нормативный порядок записи `jars` → `conf`,
  причина демон-потока, причина ленивого импорта `render` внутри `inject_openlineage`, причина
  отсутствия аргументов у `_validate_cfg` под `lru_cache`, причина запрета литерала с Jinja.
  Уходят: пересказ кода и повтор одного правила в четырёх docstring'ах — вместо повтора ссылка
  на модуль-владелец правила.

## Что не трогается

`apply_policy`, `reset_state`, порядок гейтов в `parse.inject_openlineage`, тексты warning'ов
(их попарную различимость проверяет `test_failure_reasons_are_pairwise_distinct`), `hadoop_conf.py`,
формат Variable `openlineage_config`, имя макроса `__openlineage_v1`.

## Тесты

`tests/test_ol_policy.py` правится вместе с кодом: он адресуется к `render._emit`,
`render._merge_jars_pair`, `merge_jars`, `merge_listeners`, `variable._validate_cfg` напрямую.
Новых тестов задача не требует — набор уже покрывает все затронутые ветки; задача 0 возвращает
ему способность падать.

## Критерий готовности

`python -m pytest airflow/config/tests` собирает весь набор (не 12 тестов) и зелёный после каждой
задачи. Эталон снимается сразу после задачи 0 и сверяется по числу собранных тестов.
