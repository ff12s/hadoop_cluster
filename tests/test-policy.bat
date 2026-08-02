@echo off
setlocal EnableExtensions

echo ========================================
echo Airflow cluster policy (OpenLineage) unit tests
echo ========================================

set "AIRFLOW=hadoop-airflow"

echo.
echo 1) Container health...
set "AIRFLOW_HEALTH="
for /f %%S in ('docker inspect -f "{{.State.Health.Status}}" %AIRFLOW%') do set "AIRFLOW_HEALTH=%%S"
echo %AIRFLOW%: %AIRFLOW_HEALTH%
if not "%AIRFLOW_HEALTH%"=="healthy" (
  echo [ERROR] %AIRFLOW% is not healthy
  goto :fail
)

echo.
echo 2) Running the policy test suite inside the container...
rem -p no:cacheprovider: /opt/airflow/config смонтирован :ro, кэш писать некуда.
docker exec %AIRFLOW% python -m pytest /opt/airflow/config/tests -q -p no:cacheprovider || (
  echo [ERROR] Policy unit tests failed
  goto :fail
)

echo.
echo 3) Variable openlineage_config must be a JSON object (double-encoding guard)...
docker exec %AIRFLOW% python -c "import json; from airflow.models import Variable; v=Variable.get('openlineage_config'); d=json.loads(v); assert isinstance(d, dict), 'openlineage_config is not a JSON object: seeded with --json?'; print('openlineage_config OK:', sorted(d))" || (
  echo [ERROR] openlineage_config is missing or double-encoded
  goto :fail
)

echo.
echo ========================================
echo Airflow cluster policy tests completed
echo ========================================
echo.
pause
exit /b 0

:fail
echo.
echo ========================================
echo Airflow cluster policy tests FAILED
echo ========================================
echo.
pause
exit /b 1
