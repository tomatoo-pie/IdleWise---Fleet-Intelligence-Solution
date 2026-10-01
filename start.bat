@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    python -m venv .venv
    if errorlevel 1 goto failed
)
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto failed
echo.
echo Open http://127.0.0.1:8000 in your browser.
echo Login: manager / idlewise-local
echo Keep this window open. Press Ctrl+C to stop.
".venv\Scripts\python.exe" run.py
if errorlevel 1 goto failed
exit /b 0
:failed
echo Startup failed. Check that Python 3.11 or newer is installed and port 8000 is free.
pause
exit /b 1
