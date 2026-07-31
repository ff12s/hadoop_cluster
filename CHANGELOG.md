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

### Удалено

- Переменные окружения `OPENLINEAGE_URL`, `OPENLINEAGE_NAMESPACE`, `OPENLINEAGE_JAR` — со стороны
  Airflow (`env_example`, `docker-compose.yml`, cluster policy). Конфиг лайниджа правится в
  Admin → Variables. `OPENLINEAGE_CONFIG_RESEED` остаётся как рычаг пересева.
  `jupyter/scripts/start-jupyter.sh` по-прежнему собирает `PYSPARK_SUBMIT_ARGS` из
  `OPENLINEAGE_URL`/`OPENLINEAGE_NAMESPACE`, но задаёт их теперь только сам, своими дефолтами
  (`http://marquez:5000`, `hadoop-cluster`): из окружения контейнера они ушли вместе с якорем
  `x-versions`. Это отдельный рантайм, скрипт не трогали — но значения из `.env` в него больше
  не приезжают.
