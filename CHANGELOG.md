# Журнал изменений

Формат основан на [Keep a Changelog](https://keepachangelog.com/ru/1.1.0/).

## [Unreleased]

### Изменено

- OpenLineage cluster policy: Variable `openlineage_config` хранит `{enabled, spark_conf, openlineage_jar}`
  вместо плоского `{enabled, url, namespace}`. URI openlineage-jar'а переехал из переменной окружения
  `OPENLINEAGE_JAR` в Variable, а зонд HDFS — с парса DAG-файла на рендер таски: шедулер и DAG-bag
  больше не читают ни метастор, ни сеть. `spark.extraListeners` и jar'ы мерджатся с тем, что задал DAG
  (дедуп, DAG-значения первыми), а `spark.openlineage.transport.url` и `spark.openlineage.namespace`
  берутся из Variable даже если DAG задал свои. Итог jar-мерджа пишется в `--jars`: при заданном
  DAG'ом `jars=` Spark игнорирует `spark.jars`. Хардкод класса listener'а из политики удалён.
- OpenLineage cluster policy: неподтверждённый в HDFS jar выключает лайнидж целиком. Раньше зонд
  гейтил только ветку `jar`, и `spark.extraListeners` уезжал в conf без своего класса на classpath —
  драйвер падал `ClassNotFoundException`, а мемо зонда удерживало это состояние 300 с. Теперь ветки
  `listener`, `url` и `namespace` гейтятся тем же зондом, а причины отказа jar'а пишутся обычным
  `warning`'ом на каждый рендер, а не раз в 300 с.
- `airflow/config/airflow_local_settings.py` — точка входа, которую Airflow ищет по имени, делегирует
  в `ol_policy.apply_policy`; собственной логики в ней не осталось. Смоук `test-airflow.bat` (шаг 11)
  ходит через неё, а не мимо, — иначе неподключённая политика прошла бы проверку.
- `ol_policy` разложен по фазам жизненного цикла на пять модулей (`parse`, `render`, `variable`, `probe`,
  `operator`) с фасадом `__init__.py` в 79 строк вместо одного файла на 791 строку; поведение не изменилось.
- `ol_policy` вычищен от дублей. Три копии одного мерджа CSV (`merge_jars`, `merge_listeners` и адаптер
  ветки jar'ов на рендере) заменены одной `utils.merge_csv(*sources)`; `variable._validate_cfg` отдаёт
  типизированный `Config` вместо сырого dict, поэтому рендер больше не разбирает конфиг заново; слот
  демон-потока зонда перешёл с пар `("ok"/"err", значение)` на `bool | BaseException`; `operator_attrs`
  возвращает `OperatorAttrs` вместо `SimpleNamespace`. Из фасада пакета исчезли `merge_jars` и
  `merge_listeners` — вместо них `merge_csv`. Поведение политики и тексты сообщений не изменились.
- `ol_policy` причёсан по стилю: строк длиннее 120 символов не осталось, docstring'и ужаты до «почему» —
  каждое правило записано у своей функции, в остальных местах ссылка вместо повтора. `utils` и `logger`
  получили module-docstring, аннотации `logger` переведены на встроенные `dict`/`tuple`.

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
