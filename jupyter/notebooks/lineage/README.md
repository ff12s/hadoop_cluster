# Lineage demo: PySpark → OpenLineage → Marquez

Набор ноутбуков, которые порождают разнообразные события OpenLineage и наблюдаемые
артефакты в Marquez UI: column-level lineage, schema versions, job versions, runs.

## Предварительные условия

- Кластер запущен **с профилем Jupyter**: `start-cluster.bat --with-jupyter` из корня
  репозитория. JupyterLab вынесен в опциональный профиль compose и обычным
  `start-cluster.bat` не поднимается. Ноутбукам из раздела Kyuubi нужен ещё и
  `--with-kyuubi`, а поднять всё сразу можно флагом `--all`.
- Marquez API: <http://localhost:5000>, UI: <http://localhost:3000>.
- JupyterLab: <http://localhost:8888>.
- Namespace в Marquez — `hadoop-cluster` (из `spark.openlineage.namespace`).

## Порядок запуска

Прогонять последовательно, начиная с `00_setup.ipynb`. Каждый последующий
ноутбук опирается на таблицы из предыдущих.

> ⚠️ **Между ноутбуками делайте `Kernel → Restart`.** Каждый ноутбук создаёт новый
> `SparkSession` с уникальным `appName`. Если SparkContext уже активен в текущем
> kernel, `getOrCreate()` молча проигнорирует `.appName(...)` и lineage events
> уйдут под чужим именем. В каждом ноутбуке есть `assert spark.sparkContext.appName == ...`,
> который ловит этот случай.
>
> ⏱ **И дождитесь, пока предыдущий YARN application перейдёт в `FINISHED`** на
> <http://localhost:8088> прежде чем стартовать следующий. Кластер настроен на
> `executor.instances=1`, и параллельно с Kyuubi-engine'ом второе приложение
> может зависнуть в `ACCEPTED` из-за нехватки ресурсов.

| #  | Ноутбук                       | Что демонстрирует                                                                 | Output (Hive)                       |
|----|-------------------------------|-----------------------------------------------------------------------------------|-------------------------------------|
| 00 | `00_setup.ipynb`              | Базовые синтетические таблицы — источники для всего остального                    | `raw_customers`, `raw_orders`, `raw_products` |
| 01 | `01_projections.ipynb`        | Column lineage 1:1 (identity transformation)                                      | `stg_customers_basic`               |
| 02 | `02_expressions.ipynb`        | Многие→1 column lineage с выражениями, cast, конкатенациями (single source)       | `stg_customers_enriched`            |
| 03 | `03_aggregations.ipynb`       | GROUP BY: identifier (group-key) и aggregation transformations                    | `agg_customer_stats`                |
| 04 | `04_joins.ipynb`              | Column lineage из 3 input dataset'ов через INNER + LEFT JOIN                      | `customer_orders_enriched`          |
| 05 | `05_schema_versions.ipynb`    | 3 schema versions одного и того же датасета (add / rename / drop колонок)        | `stage_customers` (×3 версии)       |
| 06 | `06_job_versions.ipynb`       | 3 job versions одного job'а (разный план, разный column lineage)                  | `customer_summary` (×3 версии)      |
| 07 | `07_multiple_runs.ipynb`      | 5 runs одной job version (идентичный план, разные timestamps)                     | `daily_country_totals`              |
| 08 | `08_chain_pipeline.ipynb`     | 3-хоповая цепочка через intermediate Hive-таблицы (raw → tmp → tmp → mart)        | `tmp_orders_with_customer`, `tmp_country_metrics`, `mart_top_countries` |
| 09 | `09_parquet_schema_drift.ipynb` | Path-based Parquet датасет на HDFS, 3 append-batch'а с разной схемой, 24 файла | `hdfs:///tmp/lineage/events_parquet` (×3 schema versions) |

## Что увидеть в Marquez

### Datasets
В UI → `Datasets` (с фильтром namespace = `hadoop-cluster`) — все таблицы из колонки Output.
В detail panel: схема, история versions, lineage граф с column-level стрелками.

### Jobs
В UI → `Jobs` (namespace = `hadoop-cluster`). Имена job'ов формируются как
`{app_name}.{spark_action_or_output_table}` — точная форма зависит от Spark-операции
и версии OL-Spark, поэтому проще находить через API по фрагменту имени (см. ниже).

Каждый job имеет:
- **Versions** — разные логические планы;
- **Runs** — отдельные запуски;
- **Lineage** — граф input → output.

### Column lineage
Внутри dataset detail (или в job's lineage view) для каждой колонки виден
список upstream-источников. Per [OL spec](https://openlineage.io/docs/spec/facets/dataset-facets/column_lineage_facet)
у каждого ребра есть `type` (`DIRECT` | `INDIRECT`) и `subtype`:

- **DIRECT**: `IDENTITY`, `TRANSFORMATION`, `AGGREGATION`
- **INDIRECT**: `JOIN`, `GROUP_BY`, `FILTER`, `SORT`, `WINDOW`, `CONDITIONAL`

`EXPRESSION` — это разговорный термин; в JSON-фасете будет `TRANSFORMATION`.

## Быстрая API-проверка

```bash
# Список всех jobs
curl -s 'http://localhost:5000/api/v1/namespaces/hadoop-cluster/jobs' \
  | jq '.jobs[].name'

# Список всех datasets
curl -s 'http://localhost:5000/api/v1/namespaces/hadoop-cluster/datasets' \
  | jq '.datasets[].name'

# Schema versions конкретного датасета (после 05)
curl -s 'http://localhost:5000/api/v1/namespaces/hadoop-cluster/datasets/default.stage_customers/versions' \
  | jq '.versions[].fields | map(.name)'

# Все runs конкретного job (имя найти по фрагменту)
JOB=$(curl -s 'http://localhost:5000/api/v1/namespaces/hadoop-cluster/jobs' \
  | jq -r '.jobs[] | select(.name | contains("daily_country_totals")) | .name')
curl -s "http://localhost:5000/api/v1/namespaces/hadoop-cluster/jobs/${JOB}/runs" \
  | jq '.runs | length'
```

## Если column lineage не появляется

Column lineage в OpenLineage-Spark включён **по умолчанию** — отдельного флага
`enabled=true` не существует. Если у выходного датасета нет column-level стрелок,
по убыванию вероятности проверь:

1. **Версия OL-Spark < 1.38.** До 1.38.0 column lineage для
   `CreateDataSourceTableAsSelectCommand` (`saveAsTable`) на Hive-каталоге не
   эмитился. Текущий пин — `OPENLINEAGE_VERSION` в `.env`. Поднять до 1.38+ и
   пересобрать и поднять заново: `docker compose build spark-image jupyter kyuubi`, затем
   `start-cluster.bat --all` (обычный `docker compose up -d` профильные Jupyter и Kyuubi
   не поднимет).
2. **Spark `eventLog.dir` недоступен** — listener иногда падает молча. Проверь
   логи драйвера на `OpenLineage` warnings.
3. **Marquez не отвечает** на `http://marquez:5000` — events теряются на HTTP
   transport. `curl http://localhost:5000/api/v1/namespaces`.
4. **Имя датасета не совпадает** с тем, что ты ищешь в UI. Marquez сохраняет
   фактическое namespace+name из факта. Загляни в полный список:
   `curl http://localhost:5000/api/v1/namespaces/hadoop-cluster/datasets | jq '.datasets[].name'`.

Опционально можно переключить представление column lineage в Marquez на
"dataset-mode" (более компактный граф):

```
spark.openlineage.columnLineage.datasetLineageEnabled  true
```

Это **не включает** lineage (он и так есть), а меняет форму facet'а: вместо
`fields[]` будет `dataset[]`-список. Marquez UI это понимает.

## Сброс состояния

Если хочется начать с чистого листа:

```sql
-- через kyuubi/beeline или прямо в notebook
DROP TABLE IF EXISTS default.raw_customers;
DROP TABLE IF EXISTS default.raw_orders;
DROP TABLE IF EXISTS default.raw_products;
DROP TABLE IF EXISTS default.stg_customers_basic;
DROP TABLE IF EXISTS default.stg_customers_enriched;
DROP TABLE IF EXISTS default.agg_customer_stats;
DROP TABLE IF EXISTS default.customer_orders_enriched;
DROP TABLE IF EXISTS default.stage_customers;
DROP TABLE IF EXISTS default.customer_summary;
DROP TABLE IF EXISTS default.daily_country_totals;
DROP TABLE IF EXISTS default.tmp_orders_with_customer;
DROP TABLE IF EXISTS default.tmp_country_metrics;
DROP TABLE IF EXISTS default.mart_top_countries;
```

И HDFS-парquet датасет из 09:

```bash
docker compose exec hadoop hdfs dfs -rm -r -skipTrash /tmp/lineage/events_parquet
```

Историю Marquez при необходимости можно стереть через `docker compose down -v`
(удалит и Postgres volume — все lineage events пропадут).
