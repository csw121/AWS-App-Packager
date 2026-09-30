@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Run setup.cmd first. This launcher never installs packages.
  exit /b 1
)
".venv\Scripts\python.exe" -m aws_app_packager.launcher %*
exit /b %errorlevel%
