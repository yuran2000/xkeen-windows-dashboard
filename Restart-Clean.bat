@echo off
:: Clean restart of xray-dashboard.
:: Читает DASHBOARD_PORT из config_local.py — поддерживает несколько инстансов с разными портами.
:: Имя task'а: XrayDashboard (порт 5000) или XrayDashboard-<port> (другие порты).
:: Self-elevates via UAC if not admin. Double-click from Explorer.

cd /d "%~dp0"

NET FILE 1>NUL 2>NUL
if not '%errorlevel%' == '0' (
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs -ArgumentList '/d %CD%'"
    exit /b
)

REM Читаем DASHBOARD_PORT из config_local.py (default 5000)
set DPORT=5000
if exist "config_local.py" (
    if exist ".venv\Scripts\python.exe" (
        for /f "delims=" %%p in ('".venv\Scripts\python.exe" -c "import config_local as c; print(getattr(c,'DASHBOARD_PORT',5000))" 2^>nul') do set DPORT=%%p
    )
)
if "%DPORT%"=="" set DPORT=5000

REM Имя task'а: XrayDashboard на 5000, XrayDashboard-<port> на других
set TASKNAME=XrayDashboard
if not "%DPORT%"=="5000" set TASKNAME=XrayDashboard-%DPORT%

echo === Restart task: %TASKNAME% (port %DPORT%) ===
schtasks /End /TN "%TASKNAME%" 2>nul

echo === Waiting for port %DPORT% release ===
timeout /t 3 /nobreak >nul

echo === Starting task %TASKNAME% ===
schtasks /Run /TN "%TASKNAME%"

echo.
echo === Verifying (ждём поднятия порта, до ~30с) ===
powershell -NoProfile -Command ^
  "$port = %DPORT%; $l = $null; for ($i=0; $i -lt 15; $i++) { $l = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue; if ($l) { break }; Start-Sleep -Seconds 2 }; if ($l) { Write-Host ('Port ' + $port + ' LISTEN: OK (PID ' + ($l.OwningProcess -join ',') + ')') -Foreground Green } else { Write-Host ('ERROR: nothing listening on :' + $port + ' after ~30s - run start.bat in a cmd window to see the error') -Foreground Red }; $py = Get-Process pythonw -ErrorAction SilentlyContinue; if ($py) { Write-Host ('pythonw processes: ' + $py.Count + ' (normal = 2: launcher + interpreter per instance)') -Foreground Gray }"

echo.
pause
