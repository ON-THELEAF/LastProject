@echo off
chcp 65001 >nul
rem BridgeDocs first-time setup: Python packages, screen build, AI models (about 9GB).
rem Needs: Git, Python 3.11, Node.js, NVIDIA GPU 12GB+ and internet.
cd /d "%~dp0"
set "VENV=%USERPROFILE%\.venvs\bridgedocs"
set "PY=%VENV%\Scripts\python.exe"
if not exist "%PY%" (
  py -3.11 -m venv "%VENV%" || (echo [error] Python 3.11 not found. Install it from python.org. & pause & exit /b 1)
)
"%PY%" -m pip install --upgrade pip
"%PY%" -m pip install -r SERVER\requirements.txt || (echo [error] Python package install failed. & pause & exit /b 1)
pushd Front
call npm install --no-audit --no-fund || (popd & echo [error] npm install failed. Install Node.js. & pause & exit /b 1)
call npm run build || (popd & echo [error] Screen build failed. & pause & exit /b 1)
popd
"%PY%" SERVER\download_models.py || (pause & exit /b 1)
echo.
echo Setup done. From now on just run "BridgeDocs 실행.bat".
pause
