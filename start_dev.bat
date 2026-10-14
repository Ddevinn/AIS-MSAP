@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Project Python environment is missing.
    exit /b 1
)
".venv\Scripts\python.exe" -B scripts\dev_server.py %*
exit /b %errorlevel%
