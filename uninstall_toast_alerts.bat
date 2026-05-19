@echo off
REM uninstall_toast_alerts.bat - removes XrayToastAlerts task from Task Scheduler.
REM Auto-elevates via UAC.
REM Pure ASCII to avoid cmd.exe parser issues with UTF-8.

cd /d "%~dp0"

REM Auto-elevate if not admin
NET FILE 1>NUL 2>NUL
if not '%errorlevel%' == '0' (
    echo Need admin rights - requesting via UAC...
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs -ArgumentList '/d %CD%'"
    exit /b
)

set DPORT=5000
if exist "config_local.py" (
    if exist ".venv\Scripts\python.exe" (
        for /f "delims=" %%p in ('".venv\Scripts\python.exe" -c "import config_local as c; print(getattr(c,'DASHBOARD_PORT',5000))" 2^>nul') do set DPORT=%%p
    )
)
set TASKNAME=XrayToastAlerts
if not "%DPORT%"=="5000" set TASKNAME=XrayToastAlerts-%DPORT%

echo === Removing task '%TASKNAME%' ===
schtasks /End /TN "%TASKNAME%" 2>nul
schtasks /Delete /TN "%TASKNAME%" /F

if errorlevel 1 (
    echo Task '%TASKNAME%' not found (probably already removed).
) else (
    echo OK: task removed.
)
echo.
pause
