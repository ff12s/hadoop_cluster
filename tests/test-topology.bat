@echo off
echo ========================================
echo Compose topology assertions
echo ========================================
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0test-topology.ps1"
exit /b %errorlevel%
