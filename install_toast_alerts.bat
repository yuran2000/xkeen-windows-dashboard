@echo off
REM install_toast_alerts.bat
REM Registers xkeen_toast_daemon as Task Scheduler task under CURRENT user
REM (NOT SYSTEM!) with /IT flag - toast notifications need interactive session.
REM Auto-elevates via UAC (schtasks /Create requires admin even for own user).
REM File is pure ASCII to avoid cmd.exe parser issues with UTF-8.

cd /d "%~dp0"

REM Auto-elevate if not admin (UAC prompt)
NET FILE 1>NUL 2>NUL
if not '%errorlevel%' == '0' (
    echo Need admin rights - requesting via UAC...
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs -ArgumentList '/d %CD%'"
    exit /b
)

REM IMPORTANT: After UAC-elevation %USERNAME% = same user (yuran), not Administrator.
REM Elevation changes TOKEN only, not IDENTITY. So task is created under yuran correctly,
REM which is critical for toast - daemon must run in interactive yuran session.

REM Task name depends on dashboard port (for multi-instance support)
set DPORT=5000
if exist "config_local.py" (
    if exist ".venv\Scripts\python.exe" (
        for /f "delims=" %%p in ('".venv\Scripts\python.exe" -c "import config_local as c; print(getattr(c,'DASHBOARD_PORT',5000))" 2^>nul') do set DPORT=%%p
    )
)
set TASKNAME=XrayToastAlerts
if not "%DPORT%"=="5000" set TASKNAME=XrayToastAlerts-%DPORT%

echo === xkeen_toast_daemon installer ===
echo TaskName: %TASKNAME%
echo Port:     %DPORT%
echo User:     %USERNAME% (interactive)
echo.

if not exist ".venv\Scripts\pythonw.exe" (
    echo ERROR: .venv\Scripts\pythonw.exe not found.
    echo        Run install.bat first to create venv.
    pause
    exit /b 1
)

if not exist "xkeen_toast_daemon.py" (
    echo ERROR: xkeen_toast_daemon.py not found next to this script.
    pause
    exit /b 1
)

echo Creating Task Scheduler task...
schtasks /Delete /TN "%TASKNAME%" /F >nul 2>nul

REM /RU = current user, /IT = interactive (required for toast visibility)
REM /SC ONLOGON = start when user logs in (toast only visible in interactive session)
REM /RL LIMITED = no elevated privileges (daemon is just HTTP client to localhost)
schtasks /Create /TN "%TASKNAME%" /TR "\"%CD%\.venv\Scripts\pythonw.exe\" \"%CD%\xkeen_toast_daemon.py\"" /SC ONLOGON /RU "%USERNAME%" /IT /RL LIMITED /F

if errorlevel 1 (
    echo.
    echo ERROR: failed to create task. See stderr above.
    pause
    exit /b 1
)

echo.
echo === OK ===
echo Task '%TASKNAME%' created.
echo Daemon will auto-start on next login.
echo To start NOW:      schtasks /Run /TN "%TASKNAME%"
echo To stop:           schtasks /End /TN "%TASKNAME%"
echo To remove:         uninstall_toast_alerts.bat
echo.
echo Log:    %CD%\xkeen_toast_daemon.log
echo State:  %CD%\xkeen_toast_daemon.state.json
echo.

echo Starting daemon right now...
schtasks /Run /TN "%TASKNAME%"
timeout /t 2 /nobreak >nul
echo Done. Check log in 1-2 minutes - daemon should write "daemon starting".
echo.
pause
