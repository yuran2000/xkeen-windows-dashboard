@echo off
chcp 65001 > nul
title xray-dashboard (background launcher)

cd /d "%~dp0"

if not exist ".venv\Scripts\pythonw.exe" (
    echo [ERROR] venv not found. Run install.bat first.
    if /I not "%~1"=="-NoPause" pause
    exit /b 1
)

if not exist "config_local.py" (
    echo [ERROR] config_local.py not found. Run install.bat first.
    if /I not "%~1"=="-NoPause" pause
    exit /b 1
)

findstr /C:"ЗАМЕНИ_МЕНЯ_СВОИМ_ПАРОЛЕМ" config_local.py >nul
if not errorlevel 1 (
    echo [ERROR] PASSWORD ещё на placeholder. Открываю config_local.py для редактирования...
    if /I "%~1"=="-NoPause" exit /b 1
    pause
    notepad config_local.py
    exit /b 1
)

REM Читаем DASHBOARD_PORT из config_local.py (default 5000)
set "DPORT="
for /f "delims=" %%p in ('.venv\Scripts\python.exe -B -c "import config_local as c; p=int(getattr(c,'DASHBOARD_PORT',5000)); assert 1 <= p <= 65535; print(p)"') do set "DPORT=%%p"
if not defined DPORT (
    echo [ERROR] Cannot read DASHBOARD_PORT from config_local.py.
    if /I not "%~1"=="-NoPause" pause
    exit /b 1
)

REM Проверяем не запущена ли уже панель (есть ли listener на нужном порту)
powershell -NoProfile -Command "if (Get-NetTCPConnection -LocalPort %DPORT% -State Listen -ErrorAction SilentlyContinue) { exit 0 } else { exit 1 }"
if not errorlevel 1 (
    echo Уже запущена! Слушает порт %DPORT%.
    echo Открой http://localhost:%DPORT% в браузере.
    echo Если хочешь перезапустить — закрой панель ^(Task Manager → pythonw^) и запусти start-bg.bat снова.
    if /I not "%~1"=="-NoPause" pause
    exit /b 0
)

echo ============================================================
echo  Запускаю xray-dashboard в фоне ^(без окна^) на порту %DPORT%...
echo ============================================================
echo.

cmd /d /c exit 0
start "" /B ".venv\Scripts\pythonw.exe" dashboard.py
if /I "%~1"=="-NoPause" exit /b %errorlevel%

echo  Ждём 3 секунды чтобы убедиться что поднялась...
timeout /t 3 /nobreak >nul

powershell -NoProfile -Command "if (Get-NetTCPConnection -LocalPort %DPORT% -State Listen -ErrorAction SilentlyContinue) { Write-Host ('  [OK] Панель работает на http://localhost:%DPORT%') -Foreground Green } else { Write-Host '  [FAIL] Не удалось запустить — попробуй start.bat (в окне cmd) чтобы увидеть ошибки.' -Foreground Red }"

echo.
echo Это окно можно закрыть — панель продолжит работать.
echo Чтобы остановить — Task Manager → pythonw.exe → End task ^(или stop.bat^)
echo Чтобы автозапуск после перезагрузки — install_service.bat ^(нужны админ-права^)
echo.
pause
