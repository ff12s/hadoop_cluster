# Ресерч: механизм отложенной инъекции OpenLineage без макро-хака

Дата: 2026-07-31. Статус: ресерч завершён, реализация не начата.
Предмет: чем заменить макрос `{{ __openlineage_v1(...) }}` в `airflow/config/ol_policy` —
механизм, которым cluster policy откладывает чтение Variable и WebHDFS-зонд с парса на воркер.
Закрывает открытый вопрос дизайна `2026-07-29-openlineage-policy-config-design.md`
(«если mutation_hook исполняется на воркере, появится альтернативная площадка инъекции»).

Все факты проверены по исходникам тегов **apache/airflow 2.6.3 и 2.10.2** и
**providers-apache-spark 4.1.1 и 4.10.0** (raw.githubusercontent.com, 2026-07-31), докам Airflow
(context7-снапшоты 2.3.4 / 2.10.5 / 2.11.0), докам и исходникам Spark 3.5 и апстрим-PR провайдера
OpenLineage. Ссылки — в конце.

---

## 1. Задача

Требования к механизму (из требований пользователя и инвариантов пакета):

1. Ноль сети и ноль Variable на парсе: парс идёт в DAG-file-processor'е шедулера,
   зависший вызов съедает бюджет разбора файла.
2. Значения (Variable `openlineage_config`, подтверждение jar в HDFS) резолвятся на воркере,
   на каждом запуске таски.
3. Мердж с DAG-значениями `spark.extraListeners` / `jars` без потерь и без задвоения.
4. Никаких правок DAG-файлов; политика ничего не роняет.
5. Работает на обеих парах пинов: Airflow 2.6.3 + провайдер 4.1.1 и Airflow 2.10.2 + 4.10.0.
6. Без костылей: текущий макро-механизм тащит три канала доставки DAG-значения,
   экранирование Jinja-литералов (`_UNSAFE_FOR_LITERAL`), мутацию `dag.user_defined_macros`
   и обработку коллизии имени макроса.

## 2. Карта окна исполнения (проверено построчно по тегам)

Порядок шагов запуска таски на воркере:

| Шаг | 2.6.3 (`taskinstance.py`) | 2.10.2 (`taskinstance.py`) |
|---|---|---|
| `prepare_for_execution()` — **shallow-копия** таски + лок | L1397 | L268 |
| listener `on_task_instance_running` | **L1402 — до рендера** | — (ниже) |
| `render_templates(context)` | L1531 | L3114 |
| Сохранение RTIF (Rendered Templates UI) | L1533–1535 | L3120–3122 |
| `task.pre_execute(context)` → `_pre_execute_hook` | L1550–1551 | L3136–3137 |
| `on_execute_callback` (`_run_execute_callback`) | L1553–1554 | L3139–3140 |
| listener `on_task_instance_running` | — (выше) | **L3142–3145 — после колбэков** |
| `execute()` | L1557–1558 | L3156–3158 |

Опорные факты (оба тега, если не сказано иначе):

- **Исключения**: из `pre_execute` — пробрасываются и роняют таску (вызов без try/except);
  из `on_execute_callback` — глотаются Airflow с `log.exception("Failed when executing execute
  callback")`, try/except стоит **вокруг каждого колбэка в цикле** (2.6.3 L1678–1687,
  2.10.2 L3193–3201).
- **`on_execute_callback` — одиночный callable или список** в обеих версиях
  (аннотация ктора `None | TaskStateChangeCallback | list[...]`, 2.6.3 L754, 2.10.2 L897).
- **`_pre_execute_hook`** есть в обеих (ктор-параметр `pre_execute`, помечен в доках
  experimental); в 2.10.2 хук запускается через `ExecutionCallableRunner` (обычная функция
  вызывается как есть, подавления исключений нет).
- **`setattr` на таске в окне исполнения легален**: `prepare_for_execution` делает shallow-копию
  и ставит `_lock_for_execution=True`; `__setattr__` под локом сначала **безусловно пишет
  атрибут**, лок лишь отключает bookkeeping (`__init_kwargs`, XComArg). Ни исключения,
  ни warning'а (2.6.3 L1055–1060, 2.10.2 L1194–1205). Мутация попадает в ту же копию, чей
  `execute()` затем выполняется.
- **`SparkSubmitOperator` не перекрывает** ни `pre_execute`, ни `on_execute_callback` ни в 4.1.1,
  ни в 4.10.0; `conf`/`jars` читаются **лениво внутри `execute()`** при построении
  `SparkSubmitHook` (4.1.1: `_get_hook` L166/L172 читает `self._conf`/`self._jars`;
  4.10.0: L183/L189 читает `self.conf`/`self.jars`). Значит любая мутация до `execute()`
  доезжает до команды `spark-submit`.
- **Воркер по умолчанию парсит настоящий DAG-файл** и переприменяет политики: `task_run` →
  `get_dag` → свежий `DagBag(dag_folder)` → `_bag_dag` → `settings.dag_policy` +
  `settings.task_policy` (2.6.3 dagbag L477/L480, 2.10.2 L515/L528).
  В 2.10.2 есть opt-in `--read-from-db` (default False): путь через `SerializedDagModel`
  **минует `bag_dag` и политики не применяются** — деплой-ограничение, см. §7.
- **Сериализация DAG**: callable-поля оператора (`_pre_execute_hook`, `on_execute_callback`)
  сериализуются **строкой исходника** (`get_python_source(var)`) и десериализуются обратно
  строками — шедулер/webserver видят безвредный текст, исполняемые callables существуют только
  там, где парсится настоящий файл (включая воркер). `user_defined_macros` из сериализации
  **исключены полностью** (`DAG.get_serialized_fields` exclusion_list).

## 3. Кандидаты

### A. `on_execute_callback`, дозапись в список — РЕКОМЕНДОВАН

`task_policy` на парсе дописывает колбэк политики в `task.on_execute_callback`
(нормализовав одиночный → список). На воркере колбэк получает отрендеренный `context`,
берёт оператор из `context["task"]` (это execution-копия), читает Variable, зондирует jar,
мерджит и пишет `conf`/`jars` через `setattr`.

За:
- Тайминг идеален: после рендера (DAG-значения — финальные строки, мердж тривиален),
  до `execute()` (лениво читающего conf/jars). Обе версии.
- **Колбэк зовёт сам `TaskInstance`**, не метод оператора → сабкласс с переопределённым
  `pre_execute`/`execute` его не отключит.
- **Айзоляция отказов бесплатно**: Airflow оборачивает каждый колбэк своим try/except.
  Баг политики не роняет таску (инвариант «политика ничего не роняет» соблюдает сам Airflow);
  упавший авторский колбэк не блокирует наш (цикл продолжается).
- **Дозапись вместо обёртки**: авторский колбэк не трогаем вообще — ни чейна, ни сентинела.
  Идемпотентность — проверить `ol_callback in callbacks` перед append.
- Не experimental (в отличие от `pre_execute`); существует и в Airflow 3 Task SDK.
- Парс-фаза политики сжимается до: гейт типа → гейт форс-выключения → append колбэка.

Против:
- Семантика «callback = наблюдатель»: мутация таски из колбэка — использование не по прямому
  назначению (то же у Databand-паттерна с pre_execute; официальные доки мутацию не описывают,
  но и не запрещают; механика подтверждена исходниками §2).
- Rendered Templates UI не покажет OL-ключи: RTIF сохраняется до колбэка. Наблюдаемость —
  лог таски (инфо-строки уже есть в `render.py`). Заметь: у макро-схемы RTIF показывал
  финальные значения — единственное, в чём она выигрывала.
- В сериализованном DAG поле появится строкой исходника колбэка (косметика webserver'а).

### B. `pre_execute` / `_pre_execute_hook`, чейн — резерв

То же окно, тот же доступ. Отличия от A, все в минус:
- исключение хука роняет таску → политика обязана сама глотать всё (у A это делает Airflow);
- авторский `pre_execute=` ктора надо чейнить (его исключение обязано пробрасываться —
  документированная семантика «raising prevents execution»), нужен сентинел от двойного чейна;
- сабкласс, переопределивший метод `pre_execute` без `super()`, молча гасит `_pre_execute_hook`
  (нужен детект `type(task).pre_execute is not BaseOperator.pre_execute`);
- параметр помечен experimental в доках обеих версий.

Community-прецедент паттерна «policy навешивает pre_execute/post_execute на все операторы» —
Databand/IBM (перепечатан официальным блогом Apache Airflow). Годный запасной вариант.

### C. Обёртка `execute` — отвергнут

Тайминг в точности апстримовый (см. §4: провайдер OL инжектит в начале `execute()`).
Но снаружи это достижимо только монкипатчем бонд-метода на инстансе; в 2.10.2 `execute`
дополнительно обёрнут `ExecutorSafeguard`. Хрупко, преимущества перед A нет.

### D. `task_instance_mutation_hook` — отвергнут (решающий факт)

Построчная проверка всех колл-сайтов:
- **2.6.3: на воркере перед исполнением НЕ вызывается.** `refresh_from_task` (L840–856) хук
  не зовёт; все четыре колл-сайта — шедулерные (`create_ti`, `_check_for_removed_or_restored_tasks`)
  либо mapped-ветки mini-scheduler'а.
- 2.10.2: вызов появился в `_refresh_from_task` (L1295–1296, «Re-apply cluster policy here…»)
  и исполняется на воркере в `_run_raw_task` — но **до** `render_templates`, и вдобавок
  в шедулере при каждом создании TI (гейт по state пришлось бы городить).
- Прозаические доки 2.10.5/2.11 («executed in a worker … just before the task instance is
  executed») для 2.6.3 просто неверны — расхождение доков с исходником, которое подозревал
  дизайн-док 29-го, подтверждено.
- Сообщество: мутация `ti.task` из хука нигде не документирована; шлейф багов
  (#20143 — не вызывается при clear из UI, #32375, #35575); в Airflow 3 хук строго
  шедулерный (AIP-72: у воркера нет метабазы) — ставка умерла бы при апгрейде.

### E. Listener `on_task_instance_running` — отвергнут

Позиция **разъезжается между версиями**: 2.6.3 — до рендера (L1402, в `_run_raw_task`),
2.10.2 — после колбэков, прямо перед `execute` (L3142). На 2.6.3 мутация технически сработала бы
(значения-литералы переживают рендер), но: механизм experimental в 2.6, регистрация — глобальным
плагином (не `airflow_local_settings`), семантика — нотификация, и listener-слот на 2.6.3 занят
legacy-пакетом `openlineage-airflow`, если его когда-нибудь поставят. Апстрим сам мутацию
в листенере не делает (их listener read-only, проверено по `listener.py`).

### F. Остальные отвергнутые (коротко)

| Механизм | Причина отказа |
|---|---|
| `{{ var.json.openlineage_config... }}` (официальный паттерн отложенного Variable) | некуда деть WebHDFS-зонд jar'а; `var.json.missing` роняет рендер таски; каналы/экранирование остаются |
| `AirflowPlugin.macros` (глобальные макросы вместо `user_defined_macros`) | чинит только регистрацию макроса; Jinja-литералы, три канала и экранирование остаются |
| Свой `SPARK_CONF_DIR`/spark-defaults для airflow-контейнера | `--conf spark.extraListeners=…` DAG'а **перезаписывает** defaults целиком (прецеденс Spark: SparkConf > flags > defaults-файл, слияния списков нет) → DAG с собственным листенером молча теряет OL; нет per-task тумблеров и зонда |
| `--properties-file` | в Spark ≤3.5 **полностью заменяет** spark-defaults.conf (SPARK-48392; opt-in merge только в Spark 4.0) |
| Connection extra спарк-коннекшна | произвольные `spark.*` не поддерживаются |
| OL-провайдер `spark_inject_parent_job_info` | провайдер OL 2.x требует Airflow ≥2.9; поддержка `SparkSubmitOperator` — только с провайдера apache-spark 5.1.1; и инжектит только `parent*`/`transport*`, не листенер |
| Статус-кво (макрос) | причина ресерча; единственное преимущество перед A — OL-значения видны в RTIF |

## 4. Апстрим-прецедент

Современный `apache-airflow-providers-openlineage` решает ту же задачу «дописать
`spark.openlineage.*` в conf перед сабмитом» так:

- **Точка инъекции — начало `execute()` самого оператора**: `self.conf =
  inject_parent_job_information_into_spark_properties(self.conf, context)` — после рендера,
  до построения команды. Не листенер (их listener read-only), не policy.
- **Гард идемпотентности**: если юзер сам задал любой `spark.openlineage.parent*` /
  `transport*` — не трогают («the integration will refrain from injecting»).
- Функции инъекции — чистые (возврат нового dict), доставка через compat-шим с no-op фолбэком.
- Дизайн-обсуждение (PR #44477, #45326, #47508): выбор «код в операторе, opt-in, default off»;
  альтернативы policy/listener в обсуждении не фигурировали (им доступен код оператора —
  нам нет, наши версии фичу не содержат).
- Legacy `openlineage-airflow` (для 2.3–2.7) conf не инжектил вовсе — только ручные макросы
  в DAG'е (`lineage_run_id` и пр.), т.е. правки DAG-файлов, от которых мы уходим.

Вывод: наша схема A — это перенос апстримового места инъекции («после рендера, до сборки
команды, мутация conf оператора») из недоступного нам кода оператора в доступный снаружи
`on_execute_callback`. Два контракта апстрима стоит зеркалить: гард «`spark.openlineage.*`
уже задан юзером → не трогать» (у нас частично есть — мердж/скаляры) и чистую функцию
инъекции поверх словаря.

## 5. Spark-факты, влияющие на дизайн

- Прецеденс: SparkConf в коде > флаги `spark-submit` (`--conf`) > `spark-defaults.conf`.
  Слияния значений по ключу нет нигде — только полная перезапись. Отсюда обязательность
  собственного `merge_csv` для `extraListeners`/`jars`.
- Отсутствующий `hdfs://`-jar в `spark.jars` роняет **сам submit** клиентски:
  `FileNotFoundException` в `Client.prepareLocalResources` (branch-3.5), до старта AM.
  Инвариант 19 (зонд jar как гейт всего лайниджа) подтверждён: без зонда падение гарантировано,
  причём раньше `ClassNotFoundException`.
- `hdfs://`-jar с той же ФС не перекачивается — регистрируется YARN-ресурсом на месте
  («Source and destination file systems are the same. Not copying»).

## 6. Рекомендация

**Механизм A**: `task_policy` (парс) = гейт типа + гейт форс-выключения + идемпотентная
дозапись колбэка политики в `task.on_execute_callback`. Колбэк (воркер) = вся текущая
рендер-фаза без каналов: `variable._validate_cfg()` → `probe` → `merge_csv` по реальным
строкам → `setattr(task, attrs.conf/attrs.jars, ...)` на execution-копии.

Перенос на текущий пакет:

- Умирает: `parse._dag_channel`, `_UNSAFE_FOR_LITERAL`, `parse._macro_call`, `MACRO`,
  мутация `dag.user_defined_macros` + обработка коллизии, `render._refusal`/`_emit`
  с тремя каналами и висячими запятыми, warning «DAG-значение содержит Jinja — дедуп
  невозможен» (дедуп теперь всегда возможен: значения финальные).
- Побочно чинится: мусорные ключи при отказе (`transport.type=http`,
  `columnLineage...=true`, пустые `url`/`namespace` в conf) — отказ теперь означает
  «conf не тронут вообще».
- Остаётся почти без правок: `variable`, `probe`, `hadoop_conf`, `logger`, `utils`,
  `operator` (раскладки атрибутов, лесенка форса, passthrough-исключения).
- `apply_policy`-катчолл остаётся на парс-фазу; в колбэке верхний try/except строго говоря
  избыточен (Airflow глотает сам), но свой — дешевле и даёт наш формат warning'а.

## 7. Ограничения и открытые вопросы для плана

1. **`--read-from-db` (2.10.2)**: воркер, запущенный с этим флагом, не применяет политики —
   колбэк не будет навешан, лайнидж молча выключен. Стандартные экзекьюторы флаг не передают;
   зафиксировать в README как деплой-ограничение.
2. **RTIF/UI**: OL-ключи не видны в Rendered Templates. Решение — не компенсировать
   (лог таски достаточен) либо дополнительно логировать итоговый conf одной инфо-строкой.
3. **Идемпотентность колбэка**: политика применяется и в шедулере, и на воркере (двойной
   парс одного объекта в одном процессе — теоретически возможен через повторный `bag_dag`);
   `append` гардить проверкой присутствия. Сам колбэк обязан быть идемпотентным по conf
   (гард апстрима «`spark.extraListeners` уже содержит наш класс → дедуп через merge_csv»
   это покрывает).
4. **Сериализация**: колбэк в serialized DAG превращается в строку исходника — проверить,
   что webserver 2.10.2 рендерит Task Details без ошибок (ожидаемо да: строка, не callable).
5. **Мемо `variable._cfg` без TTL** — при переезде дать TTL-мемо как у зонда (открытый пункт
   дизайна 29-го, исполнитель облака до сих пор не зафиксирован).
6. **Mapped-таски**: остаются неподдержанными (warning), как сейчас; `partial_kwargs`-ветку
   можно исследовать отдельно — `on_execute_callback` входит в разрешённые kwargs `partial()`
   в обеих версиях (2.6.3 L239/301, 2.10.2 L261/329), т.е. дверь для будущей поддержки
   маппинга у механизма A есть, у макро-схемы не было.
7. **Airflow 3**: policies и `on_execute_callback` живы в Task SDK; у воркера нет метабазы
   (AIP-72) — чтение Variable в колбэке пойдёт через Task API; зонд WebHDFS не зависит.
   Механизм A переживает апгрейд, D — нет.

## 8. Источники

Исходники (raw.githubusercontent.com, теги, 2026-07-31):
- `2.6.3/airflow/models/taskinstance.py` (порядок L1397–1558, `_run_execute_callback` L1678),
  `baseoperator.py` (ктор L754/L819/L823, `pre_execute` L1175, `__setattr__` L1055,
  `prepare_for_execution` L1138), `dagrun.py` (mutation-hook колл-сайты L952–L1102),
  `dagbag.py` (`_bag_dag` L464–486), `cli/commands/task_command.py` (L397–401),
  `utils/cli.py` (`get_dag` L218–238), `serialization/serialized_objects.py` (callable → L446).
- `2.10.2/...` те же файлы: taskinstance L268–273, L3106–3168, L3193; baseoperator L897/L969,
  L1328–1337, L1194–1205; taskinstance `_refresh_from_task` L1295–1296; dagbag L501–534;
  cli_config `ARG_READ_FROM_DB` L653; dag.py `get_serialized_fields` L3665–3692.
- providers-apache-spark `4.1.1`/`4.10.0` `operators/spark_submit.py` (4.1.1: L126/131/153–172;
  4.10.0: L139/144/170–189).
- Провайдер OL: `providers/openlineage/src/.../utils/spark.py`, `plugins/listener.py`,
  `providers/apache/spark/src/.../spark_submit.py` (main), compat-шим.

Доки: Airflow cluster-policies (2.10.5, 2.11.0, 3.3.0, 2.3.4 — context7), best-practices
(отложенный Variable), Task SDK API; OL provider spark.html + configurations-ref
(`spark_inject_parent_job_info` 2.0.0, `transport` 2.1.0, Airflow ≥2.9); провайдер
apache-spark changelog 5.1.1 (#47508); OpenLineage docs (spark installation/usage/airflow);
legacy `OpenLineage/1.9.1/integration/airflow/plugin.py`.

PR/issues: apache/airflow #44477, #45326, #47508, #44612, #44697 (инъекция апстрима);
#20143, #32375, #35575, #15698 (mutation hook); SPARK-48392 (+ apache/spark#46709/#46782);
`apache/spark branch-3.5 yarn/Client.scala` (`prepareLocalResources`).

Community: Databand/IBM «Airflow's best kept secrets» (task_policy + pre_execute/post_execute,
2021), Astronomer cluster-policies guide, Airflow Summit 2023.
