@echo off
setlocal
cd /d "%~dp0"

set "PY=python"
where python >nul 2>&1 || set "PY=py -3"

%PY% -c "import sys; sys.exit(0 if sys.version_info[:2] >= (3, 10) else 1)" >nul 2>&1
if errorlevel 1 (
    echo Python 3.10 or newer is required.
    echo Install it from https://www.python.org/downloads/ and tick
    echo "Add python.exe to PATH" during setup.
    pause
    exit /b 1
)

if exist "data\app.pid" set /p OLD_PID=<"data\app.pid"
if defined OLD_PID taskkill /F /PID %OLD_PID% >nul 2>&1
if defined OLD_PID timeout /t 1 /nobreak >nul

set PYTHONUTF8=1
echo.
echo   Gulnaaz Job Search
echo   Dashboard: http://127.0.0.1:8765
echo   Press Ctrl+C to stop.
echo.
%PY% -u app.py
echo.
echo Bot stopped.
pause
