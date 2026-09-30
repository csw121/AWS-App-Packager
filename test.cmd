@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" exit /b 1
".venv\Scripts\python.exe" -m ruff check .
if errorlevel 1 exit /b 1
".venv\Scripts\python.exe" -m pytest %*
exit /b %errorlevel%
