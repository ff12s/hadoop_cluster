# Журнал изменений

Формат основан на [Keep a Changelog](https://keepachangelog.com/ru/1.1.0/).

## [Unreleased]

### Изменено

- OpenLineage cluster policy: Variable `openlineage_config` хранит `{enabled, spark_conf, openlineage_jar}`
  вместо плоского `{enabled, url, namespace}`. URI openlineage-jar'а переехал из переменной окружения
  `OPENLINEAGE_JAR` в Variable, а зонд HDFS — с парса DAG-файла в колбэк на воркере: шедулер и DAG-bag
  больше не читают ни метастор, ни сеть. `spark.extraListeners` и jar'ы мерджатся с тем, что задал DAG
  (дедуп, DAG-значения первыми), а `spark.openlineage.transport.url` и `spark.openlineage.namespace`
  берутся из Variable даже если DAG задал свои. Итог jar-мерджа пишется в `--jars`, а ключ
  `spark.jars` из итогового conf удаляется: его элементы уже уехали в `--jars`, и двойное объявление
  списка полагалось бы на приоритет `--jars` у spark-submit. Хардкод класса listener'а удалён.
- OpenLineage cluster policy: неподтверждённый в HDFS jar выключает лайнидж целиком. Раньше зонд
  гейтил только ветку `jar`, и `spark.extraListeners` уезжал в conf без своего класса на classpath —
  драйвер падал `ClassNotFoundException`. Теперь ветки `listener`, `url` и `namespace` гейтятся тем
  же зондом, а причины отказа jar'а дедуплицируются тем же `warn_once`, что и остальные причины.
- `airflow/config/airflow_local_settings.py` — точка входа, которую Airflow ищет по имени, делегирует
  в `ol_policy.apply_policy`; собственной логики в ней не осталось. Смоук `test-airflow.bat` (шаг 11)
  ходит через неё, а не мимо, — иначе неподключённая политика прошла бы проверку.
- ol_policy: инъекция OpenLineage переведена с Jinja-макроса (рендер) на `on_execute_callback`
  (колбэк на воркере до `execute()`). Парс только дописывает колбэк в список `on_execute_callback`;
  весь резолв (Variable, зонд jar, мердж conf) происходит на воркере после рендера Jinja. Отказ
  любого гейта оставляет таску нетронутой, больше нет стрейных ключей в conf
  (`transport.type`/`columnLineage...`) или пустых значений (url/namespace) при отказе.
- ol_policy: зонд WebHDFS получил SPNEGO-фолбэк при 401 Unauthorized (запрашивает Kerberos-токен
  через pyspnego) и один проход-ретрай: транзиентная ошибка сети или сплошные standby на первом
  проходе не выключают лайнидж таски. Дедлайн всего зонда (два прохода × два эндпоинта
  HA-пары, на 401 каждый эндпоинт стоит двух запросов — без токена и с SPNEGO-токеном) поднят до
  17 с (было 5 с), чтобы уместить оба прохода целиком.
- `ol_policy` разложен по фазам жизненного цикла на пять модулей (`parse`, `callback`, `variable`, `probe`,
  `operator`) с фасадом `__init__.py` в 79 строк вместо одного файла на 791 строку; поведение не изменилось.
- `ol_policy` вычищен от дублей. Три копии одного мерджа CSV (`merge_jars`, `merge_listeners` и адаптер
  ветки jar'ов на рендере) заменены одной `utils.merge_csv(*sources)`; `variable._validate` отдаёт
  типизированный `Config` вместо сырого dict, поэтому рендер больше не разбирает конфиг заново; слот
  демон-потока зонда перешёл с пар `("ok"/"err", значение)` на `bool | BaseException`; `operator_attrs`
  возвращает `OperatorAttrs` вместо `SimpleNamespace`. Из фасада пакета исчезли `merge_jars` и
  `merge_listeners` — вместо них `merge_csv`. Поведение политики и тексты сообщений не изменились.
- `ol_policy` причёсан по стилю: строк длиннее 120 символов не осталось, docstring'и ужаты до «почему» —
  каждое правило записано у своей функции, в остальных местах ссылка вместо повтора. `utils` и `logger`
  получили module-docstring, аннотации `logger` переведены на встроенные `dict`/`tuple`.
- ol_policy: из пакета удалено всё кэширование — TTL-мемо Variable (`variable._cfg_memo`/
  `_validated_memo` со штампами свежести), мемо зонда jar'а (`probe._jar_memo` с парой TTL
  300/30 с), TTL у дедупликации warning'ов (`logger._warned` — теперь простой `set` на процесс)
  и кэш классов исключений (`operator._passthrough_cache`; `import_module` и так бьёт в
  `sys.modules`). Кэши были мертвы: Airflow форкает свежий процесс под каждую `TaskInstance`,
  ни одно мемо не переживало таску, а единственный живой хит — повторное чтение Variable внутри
  одного колбэка — закрыт передачей значения: колбэк читает `variable._cfg()` один раз и отдаёт
  его в `variable._validate(cfg)` (бывший `_validate_cfg` без мемо). Правка Variable теперь
  подхватывается следующей таской сразу, а не через TTL. Вместе с кэшами ушли `utils.now`,
  `reset()` модулей `variable`/`probe`/`operator` и тестовая фикстура `clock`;
  `ol_policy.reset_state()` сбрасывает единственное оставшееся состояние — дедупликацию
  warning'ов логгера.

### Известные ограничения

- `airflow tasks run --read-from-db` (Airflow 2.10+) берёт таску из сериализованного DAG'а в БД:
  `on_execute_callback` там хранится как исходный текст функции (`get_python_source`), а при
  десериализации `SerializedBaseOperator` не воссоздаёт из него вызываемый объект — Airflow пытается
  вызвать получившуюся строку и на каждом запуске таски пишет в её лог `TypeError`
  (`Failed when executing execute callback`). Лайнидж в этом случае не включается, но ошибка не
  глушится молча. Используйте CLI без этого флага или полноценный запуск DAG'а.
- **Rendered Templates** в UI (Admin → DAG → Task → Rendered Templates) не показывает OL-ключи
  (`spark.extraListeners`, `spark.openlineage.transport.url`, `spark.openlineage.namespace`),
  потому что инъекция происходит в колбэке после сохранения Rendered Template Instances в БД.
  Итоговые значения можно видеть в логе задачи.
- Кэша у политики нет: Airflow форкает свежий процесс под каждую `TaskInstance`, поэтому Variable
  читается и jar зондируется один раз на запуск таски. Обратная сторона: при недоступном WebHDFS
  каждая таска платит полный двухпроходный зонд (до `_PROBE_DEADLINE_SEC`, 17 с) перед `execute()`.

### Удалено

- Переменные окружения `OPENLINEAGE_URL`, `OPENLINEAGE_NAMESPACE`, `OPENLINEAGE_JAR` — со стороны
  Airflow (`env_example`, `docker-compose.yml`, cluster policy). Конфиг лайниджа правится в
  Admin → Variables. `OPENLINEAGE_CONFIG_RESEED` остаётся как рычаг пересева.
  `jupyter/scripts/start-jupyter.sh` по-прежнему собирает `PYSPARK_SUBMIT_ARGS` из
  `OPENLINEAGE_URL`/`OPENLINEAGE_NAMESPACE`, но задаёт их теперь только сам, своими дефолтами
  (`http://marquez:5000`, `hadoop-cluster`): из окружения контейнера они ушли вместе с якорем
  `x-versions`. Это отдельный рантайм, скрипт не трогали — но значения из `.env` в него больше
  не приезжают.

### Исправлено

- Набор тестов cluster policy не выполнялся. `pytest.importorskip` стоял на уровне модуля в
  `airflow/config/tests/test_ol_policy.py` и скипал весь файл, а не пять сквозных тестов под собой:
  без установленного Airflow из 228 тестов запускалось 12. Гейт заменён на пер-тестовый `skipif`
  с локальным импортом. Зелёные прогоны, которыми подтверждались предыдущие записи журнала,
  этот набор не покрывали.
- Строковый ключ `spark.jars` больше не остаётся в итоговом conf после мерджа его элементов в
  атрибут jars: jar-список объявлялся дважды (`--jars` и `--conf spark.jars`) и работал только
  благодаря приоритету `--jars` у spark-submit. Теперь ключ удаляется из записываемого conf;
  нестроковое значение (в CSV-мердж не попадает) остаётся в conf как было — значение DAG'а не
  теряется молча.
- Смоук `test-airflow.bat` (шаги 11 и 13) проверял снятый вместе с `render.py` механизм —
  вызов удалённого `ol_policy.ol_macro` и следы Jinja-макроса в собранной команде. Шаг 11 теперь
  дёргает `ol_execute_callback` вручную, как это делает Airflow, и проверяет итоговую команду;
  шаг 13 проверяет подхват правки Variable через `variable._validate(variable._cfg())`.
- Дедлайн зонда WebHDFS (`_PROBE_DEADLINE_SEC`) не учитывал, что каждый эндпоинт при 401 стоит двух
  запросов (без токена и с SPNEGO-токеном): реальный худший случай — 16.5 с против заявленных 8.5,
  из-за чего второй проход-ретрай мог не уложиться в дедлайн ровно там, где SPNEGO и нужен. Дедлайн
  поднят до 17 с с учётом удвоенной стоимости эндпоинта.
