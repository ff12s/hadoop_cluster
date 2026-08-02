@echo off
setlocal EnableExtensions

echo ========================================
echo Airflow Testing
echo ========================================

rem После слияния init/webserver/scheduler в один контейнер оба процесса живут
rem в hadoop-airflow - отдельных WEB/SCHED больше нет.
set "AIRFLOW=hadoop-airflow"
set "OUT=%TEMP%\hadoop-cluster-airflow-test-%RANDOM%-%RANDOM%.txt"
set "DEMO_DIR=/user/hadoop/airflow_demo"
set "AGG_DATASET=/user/hadoop/airflow_demo/agg.parquet"
rem Датасеты OpenLineage лежат в неймспейсе URI хранилища, а не в job-неймспейсе
set "MARQUEZ_NS=hdfs://namenode:9000"
set "PAUSED=0"
set "PI_APP_ID="

rem Свежая logical date на каждый прогон: DagRun'ы прошлых запусков не должны
rem ни маскировать поломку, ни давать ложное падение. Она же — нижняя граница
rem свежести для записей в Marquez.
for /f "usebackq tokens=*" %%T in (`powershell -NoProfile -Command "(Get-Date).ToUniversalTime().ToString('s')"`) do set "RUN_DATE=%%T"
if not defined RUN_DATE (
  echo [ERROR] Failed to compute a logical date for the test run
  goto :fail
)
echo Logical date for this run: %RUN_DATE%

echo.
echo 1) Container health...
rem Webserver и scheduler живут в одном контейнере - healthcheck один, на webserver.
set "AIRFLOW_HEALTH="
for /f %%S in ('docker inspect -f "{{.State.Health.Status}}" %AIRFLOW%') do set "AIRFLOW_HEALTH=%%S"
echo %AIRFLOW%: %AIRFLOW_HEALTH%
if not "%AIRFLOW_HEALTH%"=="healthy" (
  echo [ERROR] %AIRFLOW% is not healthy
  goto :fail
)

echo.
echo 2) Container contents (spark-submit, yarn, java, jobs, provider)...
docker exec %AIRFLOW% bash -lc "command -v spark-submit && command -v yarn && command -v java" || (
  echo [ERROR] Required binaries missing in the Airflow image
  goto :fail
)
rem Джобы приезжают только маунтами; pyspark_pi.py — вложенным маунтом поверх
rem каталога, и при его потере на месте файла остаётся пустая заглушка.
docker exec %AIRFLOW% bash -lc "test -s /opt/airflow/jobs/pyspark_pi.py && test -s /opt/airflow/jobs/etl_generate.py && test -s /opt/airflow/jobs/etl_aggregate.py" || (
  echo [ERROR] Job files are missing or empty in /opt/airflow/jobs
  goto :fail
)
docker exec %AIRFLOW% python -c "import inspect; from airflow.providers.apache.spark.operators.spark_submit import SparkSubmitOperator as O; p=inspect.signature(O.__init__).parameters; assert 'deploy_mode' not in p, 'provider newer than expected 4.x'; print('provider signature OK')" || (
  echo [ERROR] Spark provider check failed
  goto :fail
)

echo.
echo 3) DAG import errors (must be empty)...
rem list-import-errors всегда завершается кодом 0 - утверждение делаем по выводу:
rem любая строка ошибки содержит путь к файлу DAG'а.
docker exec %AIRFLOW% airflow dags list-import-errors --output plain > "%OUT%" 2>&1 || (
  echo [ERROR] Failed to query DAG import errors
  goto :fail
)
type "%OUT%"
findstr /c:"/opt/airflow/dags" "%OUT%" >nul && (
  echo [ERROR] DAG import errors detected
  goto :fail
)

echo.
echo 4) DAGs registered...
docker exec %AIRFLOW% airflow dags list --output plain > "%OUT%" 2>&1 || (
  echo [ERROR] Failed to list DAGs
  goto :fail
)
findstr /c:"spark_pi_dag" "%OUT%" >nul || (
  echo [ERROR] spark_pi_dag not found
  goto :fail
)
findstr /c:"spark_etl_dag" "%OUT%" >nul || (
  echo [ERROR] spark_etl_dag not found
  goto :fail
)

echo.
echo 5) Clearing HDFS artifacts of previous runs...
docker exec hadoop-node hdfs dfs -rm -r -f %DEMO_DIR% || (
  echo [ERROR] Failed to clear %DEMO_DIR%
  goto :fail
)

echo.
echo 6) Running spark_pi_dag (takes a few minutes)...
call :pause_dags || goto :fail
docker exec %AIRFLOW% airflow dags test spark_pi_dag %RUN_DATE% > "%OUT%" 2>&1
if errorlevel 1 (
  type "%OUT%"
  echo [ERROR] spark_pi_dag run failed
  goto :fail
)
type "%OUT%"
rem applicationId текущего прогона: "dags test" выводит лог таски в stdout,
rem hook провайдера печатает туда id приложения YARN.
for /f "usebackq tokens=*" %%A in (`powershell -NoProfile -Command "$m=[regex]::Matches((Get-Content -Raw -Path '%OUT%'), 'application_[0-9]+_[0-9]+'); if ($m.Count -gt 0) { $m[$m.Count - 1].Value }"`) do set "PI_APP_ID=%%A"
if not defined PI_APP_ID (
  echo [ERROR] No YARN application id in the spark_pi_dag run output
  goto :fail
)
call :assert_tasks spark_pi_dag submit_pi || goto :fail

echo.
echo 7) YARN application for Pi (%PI_APP_ID%)...
rem Проверяем именно приложение этого прогона: список FINISHED хранит и старые.
docker exec hadoop-node yarn application -status %PI_APP_ID% > "%OUT%" 2>&1 || (
  echo [ERROR] Failed to query YARN application %PI_APP_ID%
  goto :fail
)
findstr /c:"Application-Name : airflow_spark_pi" "%OUT%" >nul || (
  echo [ERROR] %PI_APP_ID% is not the airflow_spark_pi application
  goto :fail
)
findstr /c:"Final-State : SUCCEEDED" "%OUT%" >nul || (
  echo [ERROR] airflow_spark_pi did not succeed
  type "%OUT%"
  goto :fail
)
echo %PI_APP_ID%: SUCCEEDED

echo.
echo 8) Running spark_etl_dag (takes a few minutes)...
docker exec %AIRFLOW% airflow dags test spark_etl_dag %RUN_DATE% || (
  echo [ERROR] spark_etl_dag run failed
  goto :fail
)
call :assert_tasks spark_etl_dag generate || goto :fail
call :assert_tasks spark_etl_dag aggregate || goto :fail
call :unpause_dags

echo.
echo 9) HDFS artifacts...
docker exec hadoop-node hdfs dfs -ls %DEMO_DIR%/raw.parquet || goto :fail
docker exec hadoop-node hdfs dfs -ls %DEMO_DIR%/agg.parquet || goto :fail

echo.
echo 10) Lineage in Marquez...
rem Датасеты Marquez переживают перезапуски стенда, поэтому одного факта наличия
rem мало: сверяем updatedAt с logical date прогона. Адресуем датасет напрямую -
rem листинг неймспейса постранично отдаёт все датасеты стенда и нужный со
rem временем выпал бы за первую страницу. Коды: 2 - датасета нет,
rem 3 - запись осталась от прошлого прогона, 1 - Marquez недоступен.
powershell -NoProfile -Command "$ErrorActionPreference='Stop'; $url='http://localhost:5000/api/v1/namespaces/' + [uri]::EscapeDataString('%MARQUEZ_NS%') + '/datasets/' + [uri]::EscapeDataString('%AGG_DATASET%'); try { $ds = Invoke-RestMethod $url } catch { if ($_.Exception.Response.StatusCode.value__ -eq 404) { exit 2 }; exit 1 }; if ([datetimeoffset]::Parse($ds.updatedAt) -lt [datetimeoffset]::Parse('%RUN_DATE%Z')) { exit 3 }"
if errorlevel 1 (
  echo [ERROR] agg.parquet is missing in Marquez or was not updated by this run
  goto :fail
)
echo Marquez lineage OK

echo.
echo 11) Cluster policy: OL-ключи и оба jar'а в фактически собранной команде...
rem Команду строит тот же код провайдера, что и при запуске таски. Проверяем
rem канал доставки jar: наш jar обязан ехать в том же --jars, что и jar DAG'а,
rem заданный и атрибутом jars, и conf["spark.jars"]. task_policy на парсе только
rem дописывает колбэк в on_execute_callback - сами значения приезжают в колбэке
rem на воркере до execute(), поэтому здесь зовём колбэк вручную, как это делает
rem Airflow, и проверяем уже итоговую собранную команду.
rem Вход - через airflow_local_settings.task_policy: именно этот модуль Airflow
rem ищет по имени, и только он доказывает, что политика вообще подключена.
rem Прямой вызов ol_policy.apply_policy проверял бы код в обход точки входа.
docker exec %AIRFLOW% python -c "import pendulum; from airflow.models import DAG; from airflow.providers.apache.spark.operators.spark_submit import SparkSubmitOperator; import ol_policy; import airflow_local_settings; d = DAG('policy_smoke', schedule=None, start_date=pendulum.datetime(2024, 1, 1)); t = SparkSubmitOperator(task_id='t', application='/opt/airflow/jobs/pyspark_pi.py', conn_id='spark_yarn', jars='mine.jar', conf={'spark.jars': 'other.jar'}, dag=d); airflow_local_settings.task_policy(t); assert ol_policy.ol_execute_callback in (t.on_execute_callback or []), 'callback not attached at parse'; ol_policy.ol_execute_callback({'task': t}); a = ol_policy.operator_attrs(t); cmd = ' '.join(t._get_hook()._build_spark_submit_command(getattr(t, '_application', None) or t.application)); assert 'spark.extraListeners' in cmd, cmd; assert 'mine.jar' in cmd and 'other.jar' in cmd, cmd; assert 'spark.jars' not in getattr(t, a.conf), 'conf[spark.jars] must be moved into --jars'; print('policy jars OK')" || (
  echo [ERROR] Cluster policy did not inject OpenLineage or lost DAG jars
  goto :fail
)

echo.
echo 12) Cluster policy: params openlineage=False убирает листенер из команды...
docker exec %AIRFLOW% python -c "import pendulum; from airflow.models import DAG; from airflow.providers.apache.spark.operators.spark_submit import SparkSubmitOperator; import ol_policy; d = DAG('policy_smoke_off', schedule=None, start_date=pendulum.datetime(2024, 1, 1)); t = SparkSubmitOperator(task_id='t', application='/opt/airflow/jobs/pyspark_pi.py', conn_id='spark_yarn', params={'openlineage': False}, dag=d); ol_policy.apply_policy(t); assert not t.on_execute_callback, 'callback must not be attached when forced off at parse'; cmd = ' '.join(t._get_hook()._build_spark_submit_command(getattr(t, '_application', None) or t.application)); assert 'extraListeners' not in cmd, cmd; assert 'openlineage' not in cmd, cmd; print('policy toggle OK')" || (
  echo [ERROR] params openlineage=False did not disable the listener
  goto :fail
)

echo.
echo 13) Variable openlineage_config подхватывается без рестарта...
docker exec %AIRFLOW% python -c "import json; from airflow.models import Variable; import ol_policy; Variable.set('openlineage_config', json.dumps({'enabled': True, 'spark_conf': {'spark.extraListeners': 'io.openlineage.spark.agent.OpenLineageSparkListener', 'spark.openlineage.transport.type': 'http', 'spark.openlineage.transport.url': 'http://marquez:5000', 'spark.openlineage.namespace': 'smoke-ns', 'spark.openlineage.columnLineage.datasetLineageEnabled': 'true'}, 'openlineage_jar': 'hdfs://namenode:9000/opt/openlineage/openlineage-spark_2.13-1.46.0.jar'})); cfg = ol_policy.variable.validate_config(ol_policy.variable.read_config()); assert cfg is not None and cfg.namespace == 'smoke-ns', 'Variable edit was not picked up'; print('variable pickup OK')" || (
  echo [ERROR] Variable openlineage_config is not picked up without a worker restart
  call :restore_variable
  goto :fail
)
call :restore_variable || goto :fail

del "%OUT%" 2>nul
echo.
echo ========================================
echo Airflow Testing completed
echo ========================================
echo.
pause
exit /b 0

rem ===========================================================================
rem Helpers
rem ===========================================================================

:pause_dags
rem На время прогонов DAG'и ставим на паузу: планировщик отбирает DagRun'ы
rem только у непаузнутых DAG'ов и иначе мог бы параллельно запустить те же
rem таски, которые ведёт "dags test".
docker exec %AIRFLOW% airflow dags pause spark_pi_dag >nul 2>&1
if errorlevel 1 (
  echo [ERROR] Failed to pause spark_pi_dag
  exit /b 1
)
rem Флаг ставим сразу: если второй pause упадёт, снимать паузу всё равно надо.
set "PAUSED=1"
docker exec %AIRFLOW% airflow dags pause spark_etl_dag >nul 2>&1
if errorlevel 1 (
  echo [ERROR] Failed to pause spark_etl_dag
  exit /b 1
)
exit /b 0

:unpause_dags
rem Возвращаем состояние, заданное AIRFLOW__CORE__DAGS_ARE_PAUSED_AT_CREATION=false.
if not "%PAUSED%"=="1" exit /b 0
docker exec %AIRFLOW% airflow dags unpause spark_pi_dag >nul 2>&1
docker exec %AIRFLOW% airflow dags unpause spark_etl_dag >nul 2>&1
set "PAUSED=0"
exit /b 0

:restore_variable
rem Возвращаем Variable к засеянному значению: шаг 13 правит её намеренно.
rem Форма и литералы обязаны совпадать с сидингом в airflow/scripts/start-airflow.sh -
rem источник значений один, дублировать env-переменные под него незачем.
docker exec %AIRFLOW% python -c "import json; from airflow.models import Variable; Variable.set('openlineage_config', json.dumps({'enabled': True, 'spark_conf': {'spark.extraListeners': 'io.openlineage.spark.agent.OpenLineageSparkListener', 'spark.openlineage.transport.type': 'http', 'spark.openlineage.transport.url': 'http://marquez:5000', 'spark.openlineage.namespace': 'hadoop-cluster', 'spark.openlineage.columnLineage.datasetLineageEnabled': 'true'}, 'openlineage_jar': 'hdfs://namenode:9000/opt/openlineage/openlineage-spark_2.13-1.46.0.jar'}))" >nul 2>&1
if errorlevel 1 (
  echo [ERROR] Failed to restore Variable openlineage_config
  exit /b 1
)
exit /b 0

:assert_tasks
rem %1 = dag_id, %2 = ожидаемый task_id. Утверждаем консервативно: и код
rem возврата CLI, и наличие task_id в состоянии success, и отсутствие упавших
rem тасок. "dags test" не возвращает код ошибки по упавшим таскам (DAG.test()
rem их проглатывает).
docker exec %AIRFLOW% airflow tasks states-for-dag-run %1 %RUN_DATE% --output plain > "%OUT%" 2>&1
if errorlevel 1 (
  echo [ERROR] Failed to read task states of %1 for %RUN_DATE%
  type "%OUT%"
  exit /b 1
)
type "%OUT%"
findstr /c:"failed" "%OUT%" >nul && (
  echo [ERROR] %1 has failed tasks
  exit /b 1
)
findstr /c:"%2" "%OUT%" | findstr /c:"success" >nul || (
  echo [ERROR] task %2 of %1 did not reach state success
  exit /b 1
)
exit /b 0

:fail
call :unpause_dags
del "%OUT%" 2>nul
exit /b 1
