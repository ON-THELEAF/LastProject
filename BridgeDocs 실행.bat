@echo off
chcp 65001 >nul
rem BridgeDocs: get the latest code from GitHub, rebuild if it changed, then run. Keep window open while program runs.
cd /d "%~dp0"
set "PY=%USERPROFILE%\.venvs\bridgedocs\Scripts\python.exe"
if not exist "%PY%" (echo [error] Run "BridgeDocs 설치.bat" first. & pause & exit /b 1)
for /f %%h in ('git rev-parse HEAD') do set "OLD=%%h"
git pull --ff-only || echo [warn] Could not update from GitHub. Running the version on this PC.
for /f %%h in ('git rev-parse HEAD') do set "NEW=%%h"
if not "%OLD%"=="%NEW%" (
  echo Updated. Installing changes...
  "%PY%" -m pip install -q -r SERVER\requirements.txt
  pushd Front & call npm install --no-audit --no-fund & call npm run build & popd
)
if not exist Front\dist\index.html (pushd Front & call npm install --no-audit --no-fund & call npm run build & popd)
"%PY%" SERVER\web_app.py
if errorlevel 1 pause
