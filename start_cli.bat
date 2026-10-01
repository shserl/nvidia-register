@echo off
REM CLI batch without GUI — register N accounts
cd /d "%~dp0"
set "PY=%~dp0.venv\Scripts\python.exe"
set "COUNT=%~1"
if "%COUNT%"=="" set "COUNT=1"
if not exist "%PY%" (
  echo Missing .venv — run start.bat once first.
  pause
  exit /b 1
)
"%PY%" -m playwright install chromium >nul 2>&1
"%PY%" -u main.py -n %COUNT%
pause
