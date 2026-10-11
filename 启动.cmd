@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Local Python environment not found. Run the installation CMD first.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -m cleanroom gui
if errorlevel 1 pause
