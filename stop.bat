@echo off
chcp 65001 > nul
title xray-dashboard stop

cd /d "%~dp0"

REM Читаем DASHBOARD_PORT из config_local.py (default 5000)
set DPORT=5000
if exist "config_local.py" (
    if exist ".venv\Scripts\python.exe" (
        for /f "delims=" %%p in ('".venv\Scripts\python.exe" -c "import config_local as c; print(getattr(c,'DASHBOARD_PORT',5000))" 2^>nul') do set DPORT=%%p
    )
)
if "%DPORT%"=="" set DPORT=5000

echo Останавливаю процессы pythonw слушающие порт %DPORT%...
echo.

powershell -NoProfile -Command "$port = %DPORT%; $pids = (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue).OwningProcess | Sort-Object -Unique; if ($pids) { $pids | ForEach-Object { $p = Get-Process -Id $_ -ErrorAction SilentlyContinue; if ($p) { Write-Host ('  Останавливаю PID ' + $_ + ' (' + $p.Name + ')'); Stop-Process -Id $_ -Force } }; Start-Sleep -Seconds 1; if (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue) { Write-Host ('  [WARN] Порт ' + $port + ' всё ещё занят') -Foreground Yellow } else { Write-Host ('  [OK] Порт ' + $port + ' свободен') -Foreground Green } } else { Write-Host ('  Никто не слушает порт ' + $port + ' — нечего останавливать.') -Foreground Gray }"

echo.
pause
