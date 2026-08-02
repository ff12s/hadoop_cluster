# Снос кэшей `ol_policy` и удаление `spark.jars` из итогового conf

Дата: 2026-08-02
Статус: утверждён (спека оформлена до ревью-гейта; реализация выполнена инлайном в той же сессии)

## Задача

Убрать из `airflow/config/ol_policy` всё кэширование и один хрупкий побочный эффект мерджа jar'ов.
Поведение политики в остальном не меняется: те же гейты, тот же порядок, те же тексты warning'ов.

## Почему кэши мертвы

Ключевой факт, который пакет сам признавал в комментарии `probe.py`: Airflow (LocalExecutor,
стандартный task runner) **форкает свежий процесс под каждую TaskInstance**. Ни одно процессное
мемо не переживает таску. Отсюда по каждому кэшу:

1. **`variable._cfg_memo` + `_validated_memo` + машинерия штампов свежести.** Единственный живой
   хит за жизнь процесса — второй `Variable.get` внутри одного запуска колбэка (гейт `enabled`,
   затем валидация). Решается передачей значения: колбэк читает `_cfg()` один раз и отдаёт его в
   `_validate(cfg)`. Вся синхронизация «валидированное протухает в такт сырому» — сложность,
   порождённая самим кэшем. Docstring `variable.py` про «исполнителя с переиспользуемыми
   процессами» противоречил комментарию `probe.py` — правда у probe.
2. **`probe._jar_memo` (TTL 300/30 с).** `jar_available` вызывается ровно один раз за колбэк →
   ноль хитов в проде. Тестовый артефакт.
3. **TTL у `logger._warned`.** Процесс живёт один парс либо одну таску — достаточно `set`.
4. **`operator._passthrough_cache`.** Ленивость импорта нужна (порядок инициализации Airflow),
   мемо — нет: `import_module` бьёт в `sys.modules`.

## Грунтинг

- Airflow **2.6.3**, провайдер spark **4.1.1** (см. спеки 2026-07-31 — окружение не менялось).
- `/apache/airflow`, запрос `"secrets cache Variable caching use_cache; task runner fork new
  process per task"` → штатный кэш Variables (`[secrets] use_cache`, `SecretCache`, TTL 900 с)
  существует, но появился в **2.7** и работает на DAG-парсинге; в 2.6.3 его нет, а политика
  читает Variable в колбэке на воркере. Свой кэш не дублирует штатный — но и не нужен: см. выше.
- Приоритет `--jars` над `spark.jars` у spark-submit — поведение `SparkSubmitArguments`
  (командная строка выигрывает у conf); на него и полагался старый дубль. Фикс убирает
  зависимость от этого приоритета, а не опирается на него.

## Правки

### 1. `variable.py`: без мемо

Остаются `VARIABLE`, `Config`, `_clean`, `_cfg` (бывший `_load_cfg` — читает и разбирает,
никогда не бросает), `_validate(cfg)`. Удаляются `_TTL_SEC`, `_now`, оба мемо, `_cfg_with_stamp`,
`_validate_cfg`, `reset()`.

### 2. `callback.py`: одно чтение, передача вниз

`_inject`: `cfg = variable._cfg()` → гейт `enabled` → `config = variable._validate(cfg)`.
Порядок гейтов не меняется.

### 3. `probe.py`: зонд без мемо

`jar_available` — только демон-поток, join под `_PROBE_DEADLINE_SEC`, интерпретация слота.
Удаляются `_MEMO_TTL_SEC`, `_MEMO_ERROR_TTL_SEC`, `_jar_memo`, `_now`, `reset()`.
Warning'и и их ключи не меняются.

### 4. `logger.py`: `set` вместо dict-с-временем

`warn_once` дедуплицирует раз на процесс. `reset()` остаётся — единственное состояние пакета.

### 5. `operator.py`: `passthrough_exceptions` без кэша

Поимённая ленивая сборка кортежа при каждом вызове; `reset()` удаляется.

### 6. `utils.py`: удаляется `now`

Больше никем не используется.

### 7. `callback._write`: `pop("spark.jars")`

Элементы `conf["spark.jars"]` уезжают в атрибут jars (`--jars`), сам ключ удаляется из
записываемого conf. Раньше список объявлялся дважды и работал только благодаря приоритету
`--jars`. Порядок записи (jars → conf) — прежний инвариант, сохранён.

### 8. `__init__.reset_state`

Зовёт только `logger.reset()`.

## Тесты

- Удаляются тесты TTL/мемо: `test_cfg_is_memoized`, `test_validate_cfg_runs_once_per_ttl`,
  `test_cfg_memo_expires_by_ttl`, `test_validate_cfg_follows_cfg_ttl`,
  `test_error_memo_expires_faster_than_found`, `test_probe_memoizes_by_jar_uri`,
  `test_probe_memo_expires`; фикстура `clock`.
- Взамен гарантии мемо — два поведенческих теста: `test_callback_reads_variable_once`
  (один запуск колбэка — один `Variable.get`) и `test_variable_edit_is_picked_up_by_next_callback`
  (правка Variable видна следующей таске сразу).
- `test_late_thread_does_not_overwrite_memo` → `test_late_thread_result_is_discarded`
  (поздний поток не меняет возвращённый `False`).
- `test_conf_jars_are_taken_into_jars_and_left_intact` → `test_conf_jars_move_into_jars_attribute`:
  ключ из conf удалён.
- `test_reset_state_calls_module_resets` → `test_reset_state_resets_warn_dedup`.
- Вызовы `variable._validate_cfg()` в тестах → `variable._validate(variable._cfg())`.

## Смоук и документация

- `tests/test-airflow.bat` шаг 11: ассерт `conf['spark.jars'] == 'other.jar'` →
  `'spark.jars' not in conf`; шаг 13: `_validate(_cfg())` вместо `reset_state()+_validate_cfg()`.
- README и CHANGELOG «Известные ограничения»: пункт про процессные мемо → «кэша нет,
  каждая таска платит полный зонд до 17 с при недоступном WebHDFS».
- CHANGELOG (Unreleased): записи, описывавшие введение TTL-мемо, обновлены до итогового
  состояния; добавлены записи про снос кэшей и про `spark.jars`.

## Вне скоупа

Проверка `scheme == "hdfs"` в `jar_path` (второй пункт критики) — отдельной задачей.
