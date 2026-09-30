@echo off
setlocal
cd /d "%~dp0"
py -3.13 -c "import struct; assert struct.calcsize('P') == 8" >nul 2>&1
if errorlevel 1 (
  echo Python 3.13 64-bit is required. Install it before setup.
  exit /b 1
)
if not exist ".venv\Scripts\python.exe" py -3.13 -m venv ".venv"
if errorlevel 1 exit /b 1
".venv\Scripts\python.exe" -m pip install -r requirements.lock.txt
if errorlevel 1 exit /b 1
".venv\Scripts\python.exe" -m pip install --no-deps --no-build-isolation -e .
if errorlevel 1 exit /b 1
echo Setup complete. Run run.cmd to open the local application.
