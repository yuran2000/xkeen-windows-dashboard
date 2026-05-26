@echo off
chcp 65001 > nul
setlocal EnableDelayedExpansion
title xray-dashboard install

cd /d "%~dp0"

echo ============================================================
echo  xray-dashboard installation
echo ============================================================
echo  Folder: %CD%
echo ============================================================
echo.

echo [1/7] Looking for Python...
set "PY_CMD="
where py >nul 2>&1 && set "PY_CMD=py"
if "!PY_CMD!"=="" ( where python >nul 2>&1 && set "PY_CMD=python" )
if "!PY_CMD!"=="" ( where python3 >nul 2>&1 && set "PY_CMD=python3" )
if "!PY_CMD!"=="" (
    echo [ERROR] Python not found in PATH.
    echo Install Python 3.10+ from https://www.python.org/downloads/
    echo and make sure "Add python.exe to PATH" is checked during install.
    pause
    exit /b 1
)
echo Found: !PY_CMD!
echo.

echo [2/7] Creating venv (if missing)...
if not exist ".venv\Scripts\python.exe" (
    !PY_CMD! -m venv .venv
    if errorlevel 1 (
        echo [ERROR] Failed to create venv with: !PY_CMD! -m venv .venv
        pause
        exit /b 1
    )
) else (
    echo Already exists, skipping.
)

echo.
echo [3/7] Updating pip...
".venv\Scripts\python.exe" -m pip install -U pip --quiet

echo.
echo [4/7] Installing dependencies...
".venv\Scripts\python.exe" -m pip install -r requirements.txt --quiet
if errorlevel 1 (
    echo [ERROR] Failed to install dependencies.
    pause
    exit /b 1
)

echo.
echo [5/7] Preparing config_local.py...
if not exist "config_local.py" (
    copy config_local.example.py config_local.py >nul
    echo Created config_local.py from example.

    REM Auto-detect free port — опрашиваем 5000-5019, ищем первый свободный.
    REM Python выводит ОДНО число, читаем напрямую через set /p (без for /f, проще).
    echo.
    echo [5b/7] Detecting free port...
    ".venv\Scripts\python.exe" setup_helper.py detect-port 5000 5020 > _port.tmp
    set "FREE_PORT="
    set /p FREE_PORT=<_port.tmp
    del _port.tmp
    if "!FREE_PORT!"=="" set "FREE_PORT=5000"

    REM Показываем какие порты заняты — чтобы юзер видел почему 5000 не подошёл
    echo.
    echo  Текущие занятые порты в диапазоне 5000-5019:
    ".venv\Scripts\python.exe" setup_helper.py busy-ports 5000 5020
    echo.
    echo  Первый свободный: !FREE_PORT!
    echo.

    REM Интерактивный prompt — можно нажать Enter (принять auto-detect) или ввести свой
    set "PORT_INPUT="
    set /p PORT_INPUT="Использовать порт !FREE_PORT!? Нажми ENTER для согласия, ИЛИ введи свой номер (например 8080): "
    if not "!PORT_INPUT!"=="" (
        set "FREE_PORT=!PORT_INPUT!"
        echo  Использую порт: !FREE_PORT!
    )

    if not "!FREE_PORT!"=="5000" (
        echo  Меняю DASHBOARD_PORT в config_local.py на !FREE_PORT!...
        ".venv\Scripts\python.exe" setup_helper.py set-port !FREE_PORT!
    ) else (
        echo  Использую стандартный порт 5000 ^(ничего не меняю в config_local.py^).
    )
) else (
    echo config_local.py already exists, keeping yours.
    echo Если хочешь поменять DASHBOARD_PORT — открой config_local.py в Notepad и поправь руками.
)

echo.
echo [6/7] Auto-generating SECRET_KEY and SUBSCRIPTION_TOKEN (only placeholders)...
".venv\Scripts\python.exe" setup_helper.py gen-secrets

REM Читаем итоговый порт
echo.
echo [7/7] Reading final port from config_local.py...
for /f "delims=" %%p in ('".venv\Scripts\python.exe" setup_helper.py get-port 2^>nul') do set "DPORT=%%p"
if "!DPORT!"=="" set "DPORT=5000"
echo Port: !DPORT!

echo.
echo ============================================================
echo  Installation complete!  Port: !DPORT!
echo ============================================================
echo.

REM Проверка PASSWORD — через Python (findstr не понимает UTF-8 русский текст).
REM exit 0 = PASSWORD ещё placeholder (нужно открыть Notepad).
REM exit 1 = PASSWORD задан юзером (Notepad не нужен).
".venv\Scripts\python.exe" setup_helper.py password-is-placeholder >nul 2>&1
if not errorlevel 1 (
    echo  PASSWORD ещё placeholder — открываю config_local.py в Notepad.
    echo  Замени строку PASSWORD = "..." на свой пароль.
    echo  Если ставишь несколько инстансов — проверь что DASHBOARD_PORT уникален ^(сейчас !DPORT!^).
    echo  Save ^(Ctrl+S^) и закрой Notepad — потом запускай start.bat.
    echo.
    pause
    notepad config_local.py
    echo.
    echo Когда закончишь — run:  .\start.bat ^(или .\start-bg.bat в фоне^)
    echo URL: http://localhost:!DPORT!
    echo.
) else (
    echo  config_local.py уже настроен ^(PASSWORD задан^) — ничего открывать не надо.
    echo  Запускай:  .\start.bat ^(в окне^) или .\start-bg.bat ^(в фоне^)
    echo  URL: http://localhost:!DPORT!
    echo.
)
pause
endlocal
