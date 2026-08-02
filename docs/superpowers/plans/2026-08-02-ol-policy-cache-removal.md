# ol_policy Cache Removal Implementation Plan

> **For agentic workers:** план оформлен по спеке
> `docs/superpowers/specs/2026-08-02-ol-policy-cache-removal-design.md`. Реализация выполнена
> инлайном в той же сессии (нарушение маршрута /flow зафиксировано); чекбоксы отражают факт.
> Оставшийся обязательный хвост — ревью-гейт и верификация.

**Goal:** Убрать из `airflow/config/ol_policy` всё кэширование и двойное объявление jar-списка
(`spark.jars` в conf после мерджа в `--jars`), не меняя остального поведения.

**Architecture:** Границы модулей не двигаются. `variable` теряет мемо и отдаёт пару чистых
функций `_cfg()` / `_validate(cfg)`; `callback` становится единственным местом, где значение
Variable живёт в течение одного запуска; `probe` — чистый зонд под дедлайном; единственное
модульное состояние пакета — дедупликация warning'ов в `logger`.

**Tech Stack:** Python 3.10, Apache Airflow 2.6.3, провайдер spark 4.1.1, pytest. Только
стандартная библиотека внутри пакета.

## Global Constraints

- Комментарии, docstring'и (PyCharm reST), сообщения логов — на русском; идентификаторы — английские.
- Полные аннотации типов, `typing.Any` запрещён, строка ≤ 120.
- Ни один модуль пакета не импортирует Airflow на уровне модуля.
- Тексты warning'ов не меняются (`test_failure_reasons_are_pairwise_distinct`).
- Прогон после каждой задачи: `python -m pytest airflow/config/tests -q` из корня `hadoop_cluster`.
- Цикл рефакторинга: тесты зелены до правки, правятся вместе с кодом, зелены после.

### Task 1: `variable.py` без мемо + `callback` передаёт значение вниз

- [x] Удалить `_TTL_SEC`, `_now`, `_cfg_memo`, `_validated_memo`, `_cfg_with_stamp`,
      `_validate_cfg`, `reset()`; `_load_cfg` переименовать в `read_config`.
- [x] `callback._inject`: `config = variable.validate_config(cfg)` вместо `variable._validate_cfg()`.
- [x] Тесты: удалить `test_cfg_is_memoized`, `test_validate_cfg_runs_once_per_ttl`,
      `test_cfg_memo_expires_by_ttl`, `test_validate_cfg_follows_cfg_ttl`; добавить
      `test_callback_reads_variable_once`, `test_variable_edit_is_picked_up_by_next_callback`;
      `_validate_cfg()` → `validate_config(read_config())` в оставшихся.

### Task 2: `probe.py` без мемо

- [x] Удалить `_MEMO_TTL_SEC`, `_MEMO_ERROR_TTL_SEC`, `_jar_memo`, `_now`, `reset()`;
      `jar_available` — поток, join, интерпретация слота, warning'и как были.
- [x] Тесты: удалить `test_error_memo_expires_faster_than_found`, `test_probe_memoizes_by_jar_uri`,
      `test_probe_memo_expires`; `test_late_thread_does_not_overwrite_memo` →
      `test_late_thread_result_is_discarded`; фикстуру `clock` удалить.

### Task 3: `logger` set-дедуп, `operator` без кэша, `utils` без `now`, `reset_state`

- [x] `logger._warned: set`, без TTL; `reset()` остаётся.
- [x] `operator.passthrough_exceptions` собирает кортеж при каждом вызове; `reset()` удалён.
- [x] `utils.now` удалён; `__init__.reset_state()` зовёт только `logger.reset()`.
- [x] Тест `test_reset_state_calls_module_resets` → `test_reset_state_resets_warn_dedup`.

### Task 4: `spark.jars` уходит из итогового conf

- [x] `callback._write_lineage`: `dag_conf_jars = cur_conf.pop("spark.jars", None)` до мерджа;
      итоговый conf пишется без ключа.
- [x] Тест `test_conf_jars_are_taken_into_jars_and_left_intact` →
      `test_conf_jars_move_into_jars_attribute` (ключа нет, элементы в атрибуте jars).

### Task 5: смоук и документация

- [x] `tests/test-airflow.bat`: шаг 11 — `'spark.jars' not in conf`; шаг 13 —
      `validate_config(read_config())`.
- [x] README + CHANGELOG «Известные ограничения»: мемо-пункт → «кэша нет».
- [x] CHANGELOG (Unreleased): записи про TTL-мемо обновлены, добавлены записи про снос кэшей
      и `spark.jars`.

### Task 6: ревью-гейт и верификация (tail /flow)

- [ ] `review-loop` в gate mode, `GATE_FILES` = changeset; фиксы до чистого прохода.
- [ ] `verification-before-completion`: полный прогон `python -m pytest airflow/config/tests -q`,
      вывод в отчёт.
- [ ] `finishing-a-development-branch`.
