@echo off
setlocal
cd /d "%~dp0"

if /i "%~1"=="remove" (
    schtasks /Delete /TN "Gulnaaz Job Search" /F
    echo Removed "Gulnaaz Job Search" scheduled task.
    pause
    exit /b 0
)

schtasks /Create /TN "Gulnaaz Job Search" /SC ONLOGON /TR "\"%~dp0run.bat\"" /RL LIMITED /F
if errorlevel 1 (
    echo Could not create the scheduled task. Try running as Administrator.
) else (
    echo Installed: "Gulnaaz Job Search" runs at logon.
    echo Remove with: install_autostart.bat remove
)
pause
