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

### Удалено

- Переменные окружения `OPENLINEAGE_URL`, `OPENLINEAGE_NAMESPACE`, `OPENLINEAGE_JAR`.
  Конфиг лайниджа правится в Admin → Variables. `OPENLINEAGE_CONFIG_RESEED` остаётся как рычаг пересева.
