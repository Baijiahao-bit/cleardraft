@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Run the installation CMD first.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -m pip install -r requirements-dev.txt
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -m pytest --cov=cleanroom --cov-report=term-missing --cov-report=html --cov-fail-under=80
if errorlevel 1 goto failed
echo Tests passed. Open htmlcov\index.html for coverage.
pause
exit /b 0
:failed
echo Verification failed. See error above.
pause
exit /b 1
