@echo off
setlocal
cd /d "%~dp0"

echo.
echo ========================================
echo  Amazon Haul Price Monitor - UI
echo ========================================
echo.

where python >nul 2>&1
if errorlevel 1 (
  echo ERROR: Python not found on PATH.
  pause
  exit /b 1
)

if not exist "venv\Scripts\python.exe" (
  echo Creating virtual environment...
  python -m venv venv
  if errorlevel 1 (
    echo ERROR: Could not create venv.
    pause
    exit /b 1
  )
)

echo Installing / updating packages (needs internet)...
"venv\Scripts\python.exe" -m pip install --upgrade pip
"venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
  echo ERROR: pip install failed. Check internet and try again.
  pause
  exit /b 1
)

echo.
echo Starting UI - browser will open http://127.0.0.1:8787
echo Do not close this window while using the monitor.
echo.
"venv\Scripts\python.exe" web_ui.py
pause