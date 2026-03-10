@echo off
setlocal
cd /d "%~dp0"
title FinSense Stock Intelligence

echo ============================================
echo   FinSense Stock Intelligence
echo ============================================
echo.

if not exist ".venv\Scripts\python.exe" (
    echo [1/3] Creating virtual environment ...
    python -m venv .venv
    if errorlevel 1 (
        echo ERROR: python not found. Install Python 3.11+ and try again.
        pause
        exit /b 1
    )
)

echo [2/3] Installing / updating dependencies ...
.venv\Scripts\python.exe -m pip install --upgrade pip --quiet
.venv\Scripts\pip.exe install -r requirements.txt --quiet
if errorlevel 1 (
    echo ERROR: dependency install failed. Check requirements.txt.
    pause
    exit /b 1
)

echo [3/3] Starting FinSense server on http://127.0.0.1:8090 ...
echo.
echo   Dashboard will open in your browser automatically.
echo   Press Ctrl+C in this window to stop the server.
echo.

start "" "http://127.0.0.1:8090/"
.venv\Scripts\uvicorn.exe finsense.api.main:app --host 127.0.0.1 --port 8090 --reload
pause
