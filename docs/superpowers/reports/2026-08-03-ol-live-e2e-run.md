# Живая E2E-проверка ol_policy и openlineage-namespace-resolver — отчёт о прогоне

Дата: 2026-08-03
Ветка: `feature/openlineage-per-runtime-injection`
План: `docs/superpowers/plans/2026-08-02-ol-live-e2e-verification.md` (b84e5d7)
Спека: `docs/superpowers/specs/2026-08-02-ol-live-e2e-verification-design.md` (37c13ff)
Журнал задач: `.superpowers/sdd/2026-08-02-ol-live-e2e-verification/progress.md`

## Контур

| Компонент | Версия |
|---|---|
| Marquez (`hadoop-marquez`, `docker inspect --format "{{.Config.Image}}"`) | `marquezproject/marquez:0.47.0` |
| openlineage-spark jar (залит в HDFS `/opt/openlineage/`) | `openlineage-spark_2.13-1.46.0.jar` (40339269 байт) |
| Resolver jar (свой, `openlineage-namespace-resolver/`) | `openlineage-namespace-resolver.jar` (6482 байт) |
| pgjdbc jar (из `hadoop-hive:/opt/hive/lib/`) | `postgresql-42.2.23.jar` (1005522 байт) |
| Вариант `base` | Airflow **2.6.3**, образ `hadoop-cluster-airflow:2.6.3` |
| Вариант `cloud` | Airflow **2.10.2**, образ `hadoop-cluster-airflow:2.10.2` |
| Провайдеры `base` (`airflow/requirements.txt`, срез) | ядро 2.6.3 + `apache-airflow-providers-apache-spark`, `postgres` — версии дословно из исходного файла |
| Провайдеры `cloud` (`airflow/requirements_cloud.txt`, срез) | `apache-airflow-providers-apache-spark==4.10.0`, `apache-airflow-providers-openlineage==1.11.0`, плюс `postgres`, `celery`, `fab` |

Оба варианта ставили **срез** реквайрментов (`*_slim.txt`), а не полные файлы — см. раздел «Что осталось непроверенным».

## Вариант base — итог прогона

```
============================= test session starts =============================
tests\live\test_ol_policy_e2e.py::test_spark_etl_dag_emits_lineage_to_marquez PASSED [ 25%]
tests\live\test_ol_policy_e2e.py::test_spark_etl_dag_lineage_graph_has_edges PASSED [ 50%]
tests\live\test_resolver_e2e.py::test_with_resolver_multihost_namespace_is_normalized PASSED [ 75%]
tests\live\test_resolver_e2e.py::test_without_resolver_multihost_namespace_poisons_marquez PASSED [100%]
======================== 4 passed in 112.81s (0:01:52) ========================
```

Airflow 2.6.3, Marquez 0.47.0, openlineage-spark 1.46.0. Это четвёртый прогон подряд — первые три
падали (см. раздел «Находки», Task 9), и падения не были поломкой продукта.

## Вариант cloud — итог прогона

```
============================= test session starts =============================
tests\live\test_ol_policy_e2e.py::test_spark_etl_dag_emits_lineage_to_marquez PASSED [ 25%]
tests\live\test_ol_policy_e2e.py::test_spark_etl_dag_lineage_graph_has_edges PASSED [ 50%]
tests\live\test_resolver_e2e.py::test_with_resolver_multihost_namespace_is_normalized PASSED [ 75%]
tests\live\test_resolver_e2e.py::test_without_resolver_multihost_namespace_poisons_marquez PASSED [100%]
======================== 4 passed in 117.38s (0:01:57) ========================
```

Airflow 2.10.2 поднят поверх той же схемы Postgres, что создал образ 2.6.3: `start-airflow.sh`
сам выбрал `airflow db migrate` (в логе — шаги alembic), развилка задачи 4 подтверждена живьём, а
не только юнит-тестом. `template_fields` у `SparkSubmitOperator` в 2.10.2 отдаёт публичные
`conf`/`jars` (на 2.6.3 — приватные `_conf`/`_jars`); политика проверена на обеих раскладках живьём.
`AIRFLOW__OPENLINEAGE__DISABLED=true` — нативный провайдер openlineage 1.11.0 выключен, в Marquez
шёл только поток слушателя Spark.

## Нормализация namespace — измеренное доказательство

DAG `spark_jdbc_lineage_dag` подключается к PostgreSQL по multi-host JDBC URL
`jdbc:postgresql://postgres:5432,marquez-db:5432/hive_metastore` (оба алиаса указывают на один и тот
же контейнер `hadoop-postgres`). openlineage-spark строит из этого URL сырой namespace
`postgres://postgres:5432,marquez-db:5432` — запятая не входит в charset Marquez
(`^[a-zA-Z0-9_@+:;=/.-]{1,1024}$`).

- **С резолвером** (`spark.openlineage.dataset.namespaceResolvers.default.type=normalize`, jar из
  HDFS): namespace в датасете — `postgres://marquez-db:5432+postgres:5432`. Хосты отсортированы и
  склеены `+`, ровно как задумано. Датасет `hive_metastore.public.ol_demo_sales` лежит в этом
  namespace.
- **Без резолвера** (jar и ключ резолвера временно убраны из Variable `openlineage_config`):
  событие лайниджа несёт сырой namespace `postgres://postgres:5432,marquez-db:5432` без изменений —
  Marquez не трогает payload события, санирует только при записи в таблицу `datasets`.

Оба состояния Marquez подтверждены прямым чтением `GET /api/v1/events/lineage`.

## Раздел, исправляющий спеку

Спека (`docs/superpowers/specs/2026-08-02-ol-live-e2e-verification-design.md`, раздел «Доказательный
DAG») утверждает: без резолвера «Marquez отвечает 400 на событие, датасет в графе отсутствует».
**Живой прогон эту премису опроверг.** Спека не правится задним числом — эта поправка фиксирует
расхождение с фактическим поведением.

Измеренное поведение Marquez 0.47.0 без резолвера:

1. `POST /api/v1/lineage` с сырым namespace (запятая внутри) отвечает **201** — событие
   принимается, а не отвергается.
2. Marquez сохраняет датасет под **своей же санированной** формой namespace
   (`postgres://postgres:5432_marquez-db:5432`, запятая → `_`).
3. Параллельно с этим в таблицу `namespaces` пишется **вторая строка** — под сырым именем с
   запятой (`postgres://postgres:5432,marquez-db:5432`). Обе строки созданы в один и тот же
   timestamp негативного контроля.
4. Эта вторая строка навсегда ломает `GET /api/v1/namespaces` → **500**. Инстанс сам не
   восстанавливается.
5. Штатный `DELETE /api/v1/namespaces/{ns}` (документированный soft delete) на отравленной строке
   отвечает **404** — удалить её через API нельзя.
6. Контрольный эксперимент отделил причину: `PUT`/`DELETE` на namespace с обычным именем и на
   namespace со слэшами (`scheme://host:1234`, URL-encoded) отработали чисто, **200** в обоих
   случаях. Слэши не при чём — ломает именно **запятая**.

Вывод: путь записи (приём OpenLineage-события) и путь управления (REST API namespace'ов) в Marquez
0.47.0 расходятся по допустимому charset. Это делает случай резолвера **сильнее**, чем
предполагала спека: без резолвера риск — не потеря лайниджа отдельного события (которую можно было
бы просто заметить и проигнорировать), а **необратимая поломка управляющего API всего инстанса**,
снимаемая только правкой БД или сбросом тома (`docker compose down -v`).

## Находки

| # | Дефект | Где обнаружен | Коммит-фикс |
|---|---|---|---|
| 1 | Контейнерный прогон юнит-тестов ol_policy падал: `test_unexpected_error_is_swallowed_and_logged[private]` красный в контейнере, зелёный на хосте. Не регресс этой ветки — то же самое воспроизводилось и на предсессионном `b84e5d7`. Причина: реальный Airflow импортируется лениво внутри `except operator.passthrough_exceptions():`, его `dictConfig` при первом импорте подменяет хендлеры root-логгера и снимает хендлер `caplog`. | Task 5.5 (проверка перед живым прогоном) | `14ae5b2` |
| 2 | `pyspark` подтягивался транзитивно при установке среза `requirements_cloud_slim.txt` — раздувал образ и расходился с продовой моделью (Spark-джобы едут в YARN, а не исполняются локальным pyspark). | Task 5, ревью | `d18c5ba` |
| 3 | Порядок тестов в `tests/live` — `test_namespace_resolver_e2e.py` шёл алфавитно **перед** `test_ol_policy_e2e.py`, хотя план предполагал обратное. Разрушительный негативный контроль (поражает Marquez необратимо) отрабатывал первым и ронял остальные три теста. | Первый живой прогон Task 9 | `cb2bf49` (переименование в `test_resolver_e2e.py`, чтобы алфавитный порядок совпал с нужным, плюс перестановка позитивного теста перед негативным внутри файла) |
| 4 | Готовностная проба стенда (`stand_is_up`) била по `/api/v1/namespaces` — том самом эндпоинте, который негативный контроль ломает в 500. Повторный прогон против уже отравленного стенда молча скипал весь набор целиком, что читалось бы как «зелёный», а на деле означало «ничего не запускалось». | Найдено субагентом при фиксе задачи 9.5, подтверждено контроллером на живом отравленном стенде | `5425157` (проба переведена на `GET /api/v1/events/lineage?limit=1`; отравленный стенд теперь валит прогон `pytest.exit`, а не скипает) |
| 5 | Ассерты негативного контроля и графа лайниджа были неверны по факту, не по стенду: (a) негативный контроль ждал в событиях **санированный** `_`-namespace, а Marquez кладёт в payload события сырое имя с запятой — санирование происходит только при записи в таблицу `datasets`; (b) тест графа лайниджа спрашивал `outEdges` у родительской джобы `airflow_etl_generate`, а OpenLineage вешает рёбра на дочерние джобы по каждому Spark-действию. | Чистый прогон Task 9 (2 passed / 2 failed) | `15af9ef` |

Ни одна из находок 1–5 не была поломкой самого продукта (`ol_policy` / resolver) — все они дефекты
дизайна тестов, порядка орkestrации или собственных допущений плана и спеки.

### Третий прогон task 9 — оркестрационная ошибка контроллера, не находка кода

Отдельно от таблицы: третий из четырёх прогонов задачи 9 дал `4 failed` по ошибке самого
контроллера, а не из-за кода продукта. При сбросе стенда контроллер дождался готовности только
Marquez и Airflow, не дождавшись HDFS; параллельно вызов сидинга сломался из-за несовместимости
`MSYS_NO_PATHCONV=1` с формой `cmd //c` (интерактивная сессия вместо выполнения скрипта). В
результате HDFS остался без `/opt/openlineage`, jar'ов не было, и `ol_policy` **корректно** погасила
лайнидж, не найдя jar по зонду `probe.jar_available` — это живое подтверждение, что зонд работает
как задуман. DAG упал по другой причине (jar'ы DAG'а всё равно уехали в `--jars`, spark-submit не
нашёл их). Стенд был приведён в порядок вручную, зафиксирован урок про `MSYS_NO_PATHCONV=1 cmd /c`
(не `cmd //c`), и четвёртый прогон дал зелёный итог.

## Расхождение спеки с реализацией: `OL_E2E_VARIANT`

Спека (раздел «Харнесс `tests/live/`») описывает фикстуру `variant`, читающую переменную окружения
`OL_E2E_VARIANT` (`base`/`cloud`). В фактическом `tests/live/conftest.py` такой переменной и
фикстуры нет: набор не различает вариант вообще, а просто ходит в уже поднятый контейнер
`hadoop-airflow` и Marquez, какая бы версия Airflow там ни была развёрнута. Различение вариантов на
практике делается прогоном набора дважды с переключением образа между прогонами (как в README), а
не параметром pytest. Функционально это не пробел — оба варианта реально прогонялись и оба зелёные,
— но команды с `OL_E2E_VARIANT=...` из спеки не имеют эффекта и не должны использоваться.

## Что осталось непроверенным

Раздел обязателен per бриф задачи 11 — без него отчёт читался бы как «проверено всё».

- **Полные `airflow/requirements.txt` и `airflow/requirements_cloud.txt` ни разу не устанавливались
  и не проверялись живьём.** Оба образа собраны на **срезах** (`requirements_slim.txt`,
  `requirements_cloud_slim.txt`) — только пакеты, значимые для пути cluster policy (ядро Airflow,
  провайдеры `apache-spark`, `postgres`, `celery`, `fab`, `openlineage`). Полные файлы тянут
  `cx-Oracle`, `pymssql`, `PyHive`, `hmsclient`, `gssapi`, `confluent-kafka`, `xmlsec`, `pygraphviz`,
  `mysqlclient` и (в cloud-варианте) ~737 пинов aws/azure/gcp/beam/snowflake/dev-тулинга — ни
  установка этих пакетов, ни совместимость их версий со срезом не проверялись.
- **Нативный провайдер openlineage в cloud-варианте был выключен**
  (`AIRFLOW__OPENLINEAGE__DISABLED=true`), чтобы не смешивать в Marquez два независимых источника
  событий (провайдер + слушатель Spark). Собственный поток лайниджа этого провайдера (parent-run
  linking через `macros.OpenLineageProviderPlugin`, автоматическая инъекция OL-конфига в
  `SparkSubmitOperator` без cluster policy) в этом прогоне не проверялся вовсе.
- **Даунгрейд Airflow 2.10.2 → 2.6.3 не работает.** Проверено на живом стенде: `alembic` не умеет
  разрешить более новую ревизию схемы назад. Возврат стенда на базовый образ после переключения на
  cloud-вариант требует полного сброса тома метаданных (`docker compose down -v`), а не просто смены
  тега образа.
- Резолвер в контуре не проверялся вне пары Airflow+Marquez+YARN этого стенда: путь `jupyter`
  (`tests/test-namespace-resolver.sh`) намеренно не тронут этой работой — другой рантайм, другая
  сборка classpath.
- Полный набор `tests/live` необратимо отравляет Marquez негативным контролем (`GET
  /api/v1/namespaces` → 500 до `docker compose down -v`) — оба зелёных прогона (base и cloud) сняты
  на **разных** чистых стендах, повторный прогон одного и того же стенда без сброса между вариантами
  не проверялся и не сработал бы (проба готовности теперь ловит это явно, но саму комбинацию
  «два варианта подряд без сброса» никто не гонял).
- **Docker-фолбэк `mvnd.sh` не прогонялся.** Хостовая ветка враппера проверена контрольным
  прогоном `bash mvnd.sh test` — `BUILD SUCCESS`, 12 тестов, код возврата 0. Но ветка, которая
  поднимает Maven в Docker при отсутствии хостовых `mvn`/`java`, не выполнялась ни разу:
  на машине проверки `mvn` есть в `PATH`, поэтому `mvnd.sh` всегда уходил в `exec mvn`.
- **`spark/jars/*.jar` игнорируется гит-политикой репозитория**, поэтому jar резолвера не закоммичен.
  Для свежего клона без локального Maven единственный путь получить jar — именно непрогнанный
  Docker-фолбэк `mvnd.sh`.

## Прогон юнит-тестов политики и живого набора (контрольный, финальный)

По плану (шаг 4 брифа задачи 11), прогнано непосредственно перед коммитом этого отчёта. На момент
прогона был поднят чистый стенд варианта `base` (Airflow 2.6.3, `Up 2 minutes`, Marquez отвечал
200 на `/api/v1/namespaces` до прогона):

```
.venv\Scripts\python.exe -m pytest airflow/config/tests -q -p no:cacheprovider
209 passed in 1.47s

.venv\Scripts\python.exe -m pytest tests/live -q -p no:cacheprovider
4 passed in 112.44s (0:01:52)
```

Оба набора зелёные. Как и задокументировано в README, негативный контроль этого прогона снова
отравил `/api/v1/namespaces` этого стенда (ожидаемо, это его работа) — перед следующим полным
прогоном `tests/live` стенду нужен `docker compose down -v`.

Сырые логи прогонов задач 9 и 10 (ASCII-строки без мохнатой русской кодировки консоли):
`live-base4.log` (base, 4 passed in 112.81s), `live-cloud.log` (cloud, 4 passed in 117.38s) —
в рабочем scratchpad контроллера этой сессии.
