# Дизайн: инъекция OpenLineage через on_execute_callback + SPNEGO-фолбэк зонда

Дата: 2026-07-31. Статус: спека утверждена устно, план не написан.
Предшественники: `2026-07-31-ol-injection-mechanism-research.md` (выбор механизма, факты по тегам),
`2026-07-29-openlineage-policy-config-design.md` (текущая макро-схема, инварианты 1–19).

## 1. Контекст и цель

Пакет `airflow/config/ol_policy` сейчас откладывает чтение Variable и WebHDFS-зонд с парса на
воркер через Jinja-макрос `{{ __openlineage_v1(...) }}`. Механизм работает, но тащит: три канала
доставки DAG-значения, экранирование Jinja-литералов, мутацию `dag.user_defined_macros`,
обработку коллизии имени, мусорные ключи при отказе. Ресерч 2026-07-31 выбрал замену:
**идемпотентная дозапись колбэка политики в `task.on_execute_callback`** — то же окно
«после рендера, до execute», без Jinja вообще.

Одновременно правятся найденные ревью проблемы:

1. Мусорные ключи при отказе (`transport.type=http`, `columnLineage...=true`, пустые
   `url`/`namespace`) — исчезают классом: отказ = conf не тронут.
2. Зонд без Kerberos: на кербезированном WebHDFS 401 → лайнидж молча выключен.
   Добавляется SPNEGO-фолбэк (решение пользователя: urlopen + on-401 токен + ретрай).
3. `variable._cfg` — `lru_cache` без TTL → TTL-мемо.
4. `reset_state()` лезет в приватные поля четырёх модулей → per-module `reset()`.

Не входит в scope: поддержка динамического маппинга (остаётся warning), доставка политики в
облако (открытый пункт дизайна 29-го), parent-run-факты OL-провайдера.

## 2. Грундинг-бриф

Airflow (проверено построчно по тегам 2.6.3 и 2.10.2, ресерч-док §2):

- Порядок на воркере: `prepare_for_execution()` (shallow-копия + `_lock_for_execution`) →
  `render_templates` → RTIF → `pre_execute` → **`on_execute_callback`** → (2.10.2: listener) →
  `execute`.
- `on_execute_callback`: тип `None | TaskStateChangeCallback | list[...]` в обеих версиях;
  вызов — нормализация в список + **try/except вокруг каждого колбэка** с
  `log.exception("Failed when executing execute callback")`; упавший колбэк не роняет таску и
  не блокирует остальные (2.6.3 taskinstance L1678–1687, 2.10.2 L3193–3201).
- `__setattr__` под `_lock_for_execution` пишет атрибут безусловно, лок гасит только
  bookkeeping — мутация видна `execute()` (2.6.3 baseoperator L1055–1060, 2.10.2 L1194–1205).
- `SparkSubmitOperator` не перекрывает `on_execute_callback`; `conf`/`jars` читаются лениво в
  `_get_hook()` внутри `execute()` (4.1.1 L166/172 — `self._conf`/`self._jars`;
  4.10.0 L183/189 — `self.conf`/`self.jars`).
- Воркер по умолчанию парсит настоящий DAG-файл → политика переприменяется
  (`DagBag._bag_dag`); `airflow tasks run --read-from-db` (2.10.2) политики минует —
  деплой-ограничение, фиксируется в README.
- Сериализация: callable-поля оператора уходят в serialized DAG строкой исходника
  (`get_python_source`) — webserver видит текст, не вызывает.
- Апстрим-прецедент: OL-провайдер инжектит `spark.openlineage.*` реассайном `self.conf`
  в начале `execute()`; наша точка — ближайшая доступная снаружи (PR #47508).

pyspnego (`/jborean93/pyspnego`; доки main / CPython 3.9+, репо пинит 0.9.1 (стенд) и
0.11.1 (облако) — дельта названа, сигнатура идентична и подтверждена работающим кодом
SparkAPI `app/core/auth/krb_auth.py` на 0.9.1):

- `spnego.client(hostname=<host>, service="HTTP", protocol="kerberos")` → `ctx.step()` →
  первый токен (bytes | None); заголовок `Authorization: Negotiate <base64>`.
- Без явных кредов используется системный ccache (kinit контейнера).
- Kerberos-бэкенд — optional extra (gssapi/krb5, linux-маркеры в requirements): его отсутствие
  даёт исключение в `client()`/`step()` → фолбэк «без auth» через общий catch.

WebHDFS (`/websites/hadoop_apache_stable`, WebHDFS.html «Authentication»): при включённом
Kerberos аутентификация — HTTP SPNEGO (`curl -i --negotiate`), challenge-based: сервер отвечает
401 + `WWW-Authenticate: Negotiate`, клиент повторяет запрос с `Authorization`.

Spark (ресерч-док §5): значения conf не мерджатся нигде — только полная перезапись по
прецеденсу; отсутствующий hdfs-jar в `spark.jars` роняет submit клиентски
(`FileNotFoundException` в `prepareLocalResources`) — зонд обязателен.

Ретрай зонда: tenacity есть в обоих манифестах, но зонд — stdlib-only модуль с собственным
дедлайном; двухпроходный цикл — for-loop внутри существующего демон-потока, фреймворк не нужен.

## 3. Архитектура

Фазы переименовываются по смыслу:

| Модуль | Было | Станет |
|---|---|---|
| `parse.py` | сборка макро-строк, мутация DAG | только гейты + append колбэка |
| `render.py` | `ol_macro` + каналы | **удаляется**, заменяется `callback.py` |
| `callback.py` | — | `ol_execute_callback(context)`: весь резолв и запись |
| `probe.py` | зонд без auth, один проход | + SPNEGO on-401, ретрай-проход, дифф-TTL мемо |
| `variable.py` | `lru_cache` | TTL-мемо, `reset()` |
| `logger.py`, `operator.py`, `utils.py`, `hadoop_conf.py`, `handlers.py` | | без структурных изменений (+ `reset()` где есть состояние) |

Поток управления:

```
парс (шедулер И воркер, DagBag._bag_dag → task_policy)
  apply_policy(task)
    гейт: SparkSubmitOperator? mapped? → warn/выход
    гейт: operator_attrs известна? → warn/выход
    гейт: lineage_forced(task) is False → выход (DAG нетронут)
    append: task.on_execute_callback += [ol_execute_callback]  (идемпотентно)

воркер (TaskInstance._run_execute_callback, после рендера, до execute)
  ol_execute_callback(context)
    task = context["task"]                      # execution-копия
    гейты заново: тип, раскладка, форс (лесенка params)
    cfg = variable.load()                       # TTL-мемо, никогда не бросает
    enabled/forced → отказ = return             # conf НЕ тронут
    config = validate(cfg)                      # None → return
    probe.jar_available(...)                    # SPNEGO-фолбэк + ретрай; False → return
    запись (порядок нормативен: jars раньше conf):
      setattr(task, attrs.jars, merge_csv(dag_jars, conf_jars, config.jar_uri))
      setattr(task, attrs.conf, {**cur, 5 ключей})
```

## 4. Парс-фаза (`parse.py`)

- `inject_openlineage(task)` теряет всю сборку строк. Остаётся: `operator_attrs`-гейт
  (незнакомая раскладка → warn, колбэк не вешаем — на воркере он бы всё равно отказал, но
  ранний warn на парсе виден в логах процессора), `lineage_forced(task) is False` → выход,
  append колбэка.
- Append: `existing = task.on_execute_callback`; нормализация
  (`None → []`, callable → `[callable]`, список → копия списка); если `ol_execute_callback`
  уже в списке — no-op; иначе `task.on_execute_callback = [*existing, ol_execute_callback]`.
  Авторские колбэки не оборачиваются и не переупорядочиваются: наш — последним.
- Сравнение идентичности — по самой функции (`is`), как сейчас у макроса: повторный парс
  в том же процессе даёт тот же объект функции.
- `MACRO`, `_dag_channel`, `_macro_call`, `_UNSAFE_FOR_LITERAL`, мутация
  `dag.user_defined_macros`, warning «имя занято», warning «DAG-значение содержит Jinja» —
  удаляются. Гейт «таска не привязана к DAG» больше не нужен (DAG не трогаем) — удаляется.

## 5. Колбэк-фаза (`callback.py`)

Сигнатура: `def ol_execute_callback(context: dict) -> None` (реальный тип Context —
airflow-объект, но модуль Airflow не импортирует; читается только `context["task"]`).

Верхний `try/except Exception` со своим `warn_once` — Airflow глотает и сам, но свой except
даёт наш формат сообщения и дедупликацию; `passthrough_exceptions` здесь не пробрасываются
(в колбэке нет чужих таймаутов парса — а исключение из колбэка Airflow всё равно погасит).

Гейты в порядке: тип оператора (isinstance через `operator._spark_submit_operator()`),
`operator_attrs`, `lineage_forced` (лесенка `task.params` → `dag.params` — работают и на
воркере: params смерджены в объект таски), `variable`-конфиг, `enabled`/форс, валидация полей,
зонд jar. Любой отказ → `return`, ни одного ключа в conf.

Запись при успехе (все значения — финальные строки, Jinja нигде):

| Ключ | Поведение |
|---|---|
| атрибут `jars` | `merge_csv(getattr(task, attrs.jars), cur_conf.get("spark.jars"), config.jar_uri)` — dedup, DAG-значения первыми; `cur_conf["spark.jars"]` не удаляется (инвариант 4 старого дизайна: `--jars` побеждает как источник) |
| `spark.extraListeners` | `merge_csv(dag_значение, config.listener)` |
| `spark.openlineage.transport.type` | `"http"` — литерал, только при успехе |
| `spark.openlineage.transport.url` | значение Variable; DAG-значение перебивается с инфо-логом |
| `spark.openlineage.namespace` | то же |
| `spark.openlineage.columnLineage.datasetLineageEnabled` | `"true"` — литерал, только при успехе |

Порядок двух setattr нормативен: `jars` раньше `conf` (обрыв между ними оставляет лишний jar
без листенера — безопасно; обратный порядок дал бы листенер без jar'а — сломанный запуск).

Инфо-логи мерджа/перебивания — как в текущем `render.py` (`_scalar`-лог конфликтов остаётся).

## 6. Зонд (`probe.py`): SPNEGO-фолбэк и ретрай

Демон-поток + `_PROBE_DEADLINE_SEC = 5.0` + `ENDPOINT_TIMEOUT_SEC = 2.0` — без изменений.

SPNEGO on-401 внутри `_query_endpoint`:

```
GET .../GETFILESTATUS без auth
  200 → found; 404 → absent; 403+StandbyException → standby
  401:
    token = _spnego_header(host)     # ленивый import spnego; None при любой ошибке
    token is None → outcome "error" + warn_once("kerberos-unavailable")
    повтор GET с Authorization: Negotiate <token>
      200 → found; 404 → absent; иначе → error
```

- `_spnego_header(host)`: `spnego.client(hostname=host, service="HTTP",
  protocol="kerberos")`, `ctx.step()`, `base64`. Любое исключение (нет pyspnego, нет
  kerberos-бэкенда, нет тикета, KDC недоступен) → None. Отдельного пула нет — вызов уже
  внутри демон-потока под дедлайном (решение пользователя, отличие от SparkAPI-референса
  зафиксировано).
- SPNEGO-контекст одноразовый, per-endpoint, без кэша токенов: зонд ходит раз в TTL.
- Докстрока модуля фиксирует ограничение: аутентификация — только SPNEGO/Negotiate,
  делегационные токены не поддерживаются.

Ретрай (решение пользователя «нужен ретрай, если не получилось с первого раза»):

- `_probe` делает до **двух проходов** по списку эндпоинтов. Исходы `found`/`absent` —
  терминальные немедленно (кластер ответил авторитетно). Второй проход — только если первый
  не дал ни того ни другого (сплошные `error`/`standby`), с паузой 0.5с между проходами.
  Всё под тем же дедлайном 5с: не уложились — прежний исход «deadline» снаружи.
- Мемо дифференцируется по исходу: `found`/`absent` → TTL 300с (`_MEMO_TTL_SEC`);
  `error`-исходы (включая deadline и исключения резолвера) → TTL 30с
  (`_MEMO_ERROR_TTL_SEC`) — восстановление кластера подхватывается быстро, но мёртвый
  кластер не долбится каждой таской.

## 7. `variable.py`: TTL-мемо

- `_cfg()`/`_validate_cfg()` теряют `lru_cache`; состояние — `_memo: dict[str, tuple[...]]`
  с таймстампом `utils.now()` и TTL 300с, тем же паттерном, что `_jar_memo`.
  Мемоизируется конечный результат валидации (`Config | None`) и сырой конфиг.
- Семантика warning'ов не меняется: причины пишутся при (пере)вычислении, дедупликация —
  прежним `warn_once`.

## 8. `reset()` per module

- `logger.reset()` — `_warned.clear()`; `variable.reset()` — мемо; `probe.reset()` —
  `_jar_memo.clear()`; `operator.reset()` — `_passthrough_cache = None`.
- `ol_policy.reset_state()` — только четыре вызова, приватные поля чужих модулей из
  `__init__` уходят.

## 9. Тесты (`airflow/config/tests/test_ol_policy.py`)

Умирают: все тесты каналов, `_macro_call`, экранирования, `user_defined_macros`, коллизии
имени, «refusal держит DAG-канал», рендер в SandboxedEnvironment.

Появляются:

- **Парс-фаза**: append в пустой/одиночный/списочный `on_execute_callback`; авторский колбэк
  сохранён и стоит раньше; идемпотентность повторного `apply_policy`; форс-выключение →
  колбэк не навешан; mapped/незнакомая раскладка/не-Spark — как раньше; парс не читает
  Variable и не ходит в сеть (существующие тесты переадресуются).
- **Колбэк-фаза**: полный цикл `apply_policy` → вызов колбэка с `context={"task": task}` →
  проверка conf/jars; мердж listener/jars с DAG-значениями (включая значения с кавычками и
  бэкслэшами — раньше нелитерализуемые, теперь обычные строки); перебивание url/namespace
  с логом; **отказ каждого гейта оставляет conf и jars байт-в-байт нетронутыми** (главный
  новый инвариант); колбэк никогда не бросает.
- **Зонд**: 401 → SPNEGO → повтор с заголовком (подменённый `urlopen` проверяет
  `Authorization`); SPNEGO недоступен → error + warn_once; ретрай-проход (первый проход
  errors, второй found); found/absent терминальны с первого прохода; дифф-TTL мемо
  (error-запись протухает за 30с, found — нет).
- **Variable**: TTL-протухание (через monkeypatch `utils.now`), пере-чтение после TTL.
- **conftest**: дубли операторов получают `on_execute_callback: object | None = None`
  в `__init__`; фикстура `_reset_policy_state` не меняется (зовёт агрегатор).

Тесты по-прежнему бегут без Airflow и без pyspnego: `spnego` мокается через
`sys.modules`-подмену там, где тестируется успешный токен.

## 10. Инварианты (переезжают/добавляются)

1. Политика ничего не роняет: парс — catch-all в `apply_policy`; колбэк — свой catch-all,
   плюс страховка Airflow (per-callback try/except).
2. Инвариант 19: нет подтверждённого jar — нет лайниджа целиком.
3. Порядок записи: `jars` раньше `conf`.
4. Отказ = conf/jars байт-в-байт нетронуты (новый, заменяет `_refusal`-семантику).
5. Значения Variable не попадают в warning-лог (только в инфо-лог мерджа, как сейчас).
6. Парс: ноль сети, ноль Variable, ноль мутаций DAG.
7. Ни один модуль пакета не импортирует Airflow и pyspnego на уровне модуля.

## 11. Риски и ограничения

- `--read-from-db` (2.10.2) минует политику → колбэка нет → лайниджа нет, молча. README.
- RTIF/Rendered Templates не показывает OL-ключи (инъекция после сохранения RTIF).
  Компенсация — инфо-лог итоговых значений в логе таски.
- Сабкласс, задавший `on_execute_callback` списком и **заменяющий** его в своём коде после
  парса, может потерять наш колбэк — экзотика, не детектируется, принимаем.
- kinit на воркере — предусловие SPNEGO-фолбэка (в кербезированной среде airflow-контейнер
  обязан иметь тикет); без тикета — warn + лайнидж выключен, поведение не хуже текущего.
- Стенд не кербезирован: SPNEGO-ветка на стенде мертва по построению, живой прогон —
  только юнит-тесты с моками. Риск принят (кербезированного стенда нет).

## 12. Файлы

- `airflow/config/ol_policy/__init__.py` — экспорт `ol_execute_callback` вместо `ol_macro`/
  `MACRO`, агрегатор `reset_state` из per-module `reset()`.
- `airflow/config/ol_policy/parse.py` — гейты + append.
- `airflow/config/ol_policy/callback.py` — новый (замена `render.py`).
- `airflow/config/ol_policy/probe.py` — SPNEGO, ретрай, дифф-TTL, `reset()`.
- `airflow/config/ol_policy/variable.py` — TTL-мемо, `reset()`.
- `airflow/config/ol_policy/logger.py`, `operator.py` — `reset()`.
- `airflow/config/tests/conftest.py`, `test_ol_policy.py` — по §9.
- `README`/`CHANGELOG` стенда — механизм, `--read-from-db`-ограничение, RTIF-замечание.
