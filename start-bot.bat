@echo off
REM ============================================================
REM  Start the QQ + DeepSeek bot.
REM  NapCat must already be running (start-napcat.bat first).
REM
REM  Stop it with Ctrl+C or by closing this window.
REM ============================================================
chcp 65001 >nul
setlocal
cd /d "%~dp0"

set "PY=%CD%\.venv\Scripts\python.exe"
if exist "%PY%" goto :checkdeps

echo [bot] Creating virtual environment .venv ...
python -m venv .venv
if exist "%PY%" goto :checkdeps

echo [bot] venv creation failed, falling back to system Python.
set "PY=python"

:checkdeps
"%PY%" -c "import websockets, httpx, tzdata" >nul 2>&1
if not errorlevel 1 goto :run

echo [bot] Installing dependencies ...
"%PY%" -m pip install -i https://pypi.tuna.tsinghua.edu.cn/simple -r requirements.txt
if errorlevel 1 (
    echo.
    echo [bot] Dependency install failed. Check your network and run this again.
    pause
    exit /b 1
)

:run
echo.
echo [bot] Starting ... press Ctrl+C to stop.
echo.
"%PY%" run.py

echo.
echo [bot stopped]
pause
