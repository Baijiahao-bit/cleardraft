@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if errorlevel 1 (set "TASK_PYTHON=python") else (set "TASK_PYTHON=py -3")
%TASK_PYTHON% -c "import sys; sys.exit(0 if sys.version_info >= (3,11) else 1)"
if errorlevel 1 (
  echo Python 3.11 or newer is required. Install from python.org first.
  pause
  exit /b 1
)
if not exist ".venv\Scripts\python.exe" %TASK_PYTHON% -m venv .venv
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto failed
echo Installation completed. Double-click the launcher CMD to open the app.
pause
exit /b 0
:failed
echo Installation failed. Check the error above and network connectivity.
pause
exit /b 1
