@echo off
chcp 65001 >nul
cd /d "%~dp0"

set "PY=%~dp0.venv\Scripts\python.exe"

if not exist "%PY%" (
  echo Creating venv...
  where uv >nul 2>&1
  if errorlevel 1 (
    echo uv not found. Please install uv or create .venv first.
    pause
    exit /b 1
  )
  uv venv .venv
  uv pip install -r requirements.txt
)

echo Installing Chromium if needed...
"%PY%" -m playwright install chromium >nul 2>&1

echo Starting GUI...
start "" "%PY%" "%~dp0gui.py"
exit /b 0
