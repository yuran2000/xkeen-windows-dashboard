@echo off
chcp 65001 > nul
title xray-dashboard

cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] venv not found. Run install.bat first.
    pause
    exit /b 1
)

if not exist "config_local.py" (
    echo [ERROR] config_local.py not found. Run install.bat first — it will create it.
    pause
    exit /b 1
)

findstr /C:"ЗАМЕНИ_МЕНЯ_СВОИМ_ПАРОЛЕМ" config_local.py >nul
if not errorlevel 1 (
    echo [ERROR] You still have placeholder PASSWORD in config_local.py
    echo Open config_local.py in Notepad, change PASSWORD = "..." line, save, then run start.bat again.
    pause
    notepad config_local.py
    exit /b 1
)

REM Читаем DASHBOARD_PORT из config_local.py (default 5000)
for /f "delims=" %%p in ('".venv\Scripts\python.exe" -c "import config_local as c; print(getattr(c,'DASHBOARD_PORT',5000))" 2^>nul') do set DPORT=%%p
if "%DPORT%"=="" set DPORT=5000

echo ============================================================
echo  xray-dashboard
echo ============================================================
echo  URL:      http://localhost:%DPORT%
echo  LAN URL:  http://^<your-lan-ip^>:%DPORT%  (find via: ipconfig)
echo  ВНИМАНИЕ: это окно должно оставаться открытым.
echo  Чтобы запустить в фоне без окна — используй start-bg.bat.
echo  Чтобы остановить — Ctrl+C здесь или stop.bat в другом окне.
echo  Логи дублируются в dashboard.log (на случай если окно закроется).
echo ============================================================
echo.

powershell -NoProfile -Command "& '.\.venv\Scripts\python.exe' dashboard.py 2>&1 | Tee-Object -FilePath dashboard.log"

echo.
echo ============================================================
echo  Python завершился ^(exit code %ERRORLEVEL%^)
echo  Если был crash — traceback выше И в файле dashboard.log
echo ============================================================
pause
