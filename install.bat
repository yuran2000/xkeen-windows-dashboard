@echo off
chcp 65001 > nul
title xray-dashboard install

cd /d "%~dp0"

echo ============================================================
echo  xray-dashboard installation
echo ============================================================
echo  Folder: %CD%
echo ============================================================
echo.

echo [1/7] Looking for Python...
set PY_CMD=
where py >nul 2>&1 && set PY_CMD=py
if "%PY_CMD%"=="" (
    where python >nul 2>&1 && set PY_CMD=python
)
if "%PY_CMD%"=="" (
    where python3 >nul 2>&1 && set PY_CMD=python3
)
if "%PY_CMD%"=="" (
    echo [ERROR] Python not found in PATH.
    echo Install Python 3.10+ from https://www.python.org/downloads/
    echo and make sure "Add python.exe to PATH" is checked during install.
    pause
    exit /b 1
)
echo Found: %PY_CMD%
echo.

echo [2/7] Creating venv (if missing)...
if not exist ".venv\Scripts\python.exe" (
    %PY_CMD% -m venv .venv
    if errorlevel 1 (
        echo [ERROR] Failed to create venv with: %PY_CMD% -m venv .venv
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

    REM Auto-detect free port — опрашиваем 5000-5019, ищем первый свободный
    echo.
    echo [5b/7] Detecting free port...
    ".venv\Scripts\python.exe" -c "import socket; ports=[p for p in range(5000,5020) if socket.socket().connect_ex(('127.0.0.1',p))!=0]; print('FREE_PORT='+str(ports[0]) if ports else 'FREE_PORT=5000')" > _port.tmp
    for /f "tokens=2 delims==" %%p in (_port.tmp) do set FREE_PORT=%%p
    del _port.tmp
    if "%FREE_PORT%"=="" set FREE_PORT=5000

    REM Показываем какие порты заняты — чтобы юзер видел почему 5000 не подошёл
    echo.
    echo  Текущие занятые порты в диапазоне 5000-5019:
    ".venv\Scripts\python.exe" -c "import socket; busy=[p for p in range(5000,5020) if socket.socket().connect_ex(('127.0.0.1',p))==0]; print('    ' + (', '.join(str(p) for p in busy) if busy else '(нет занятых, все свободны)'))"
    echo.
    echo  Первый свободный: %FREE_PORT%
    echo.

    REM Интерактивный prompt — можно нажать Enter (принять auto-detect) или ввести свой
    set /p PORT_INPUT="Использовать порт %FREE_PORT%? Нажми ENTER для согласия, ИЛИ введи свой номер (например 8080): "
    if not "%PORT_INPUT%"=="" (
        set FREE_PORT=%PORT_INPUT%
        echo  Использую порт: %FREE_PORT%
    )

    if not "%FREE_PORT%"=="5000" (
        echo  Меняю DASHBOARD_PORT в config_local.py на %FREE_PORT%...
        ".venv\Scripts\python.exe" -c "import re; p='config_local.py'; s=open(p,encoding='utf-8').read(); s=re.sub(r'DASHBOARD_PORT\s*=\s*\d+', 'DASHBOARD_PORT = %FREE_PORT%', s); open(p,'w',encoding='utf-8').write(s); print('  Done.')"
    ) else (
        echo  Использую стандартный порт 5000 ^(ничего не меняю в config_local.py^).
    )
) else (
    echo config_local.py already exists, keeping yours.
    echo Если хочешь поменять DASHBOARD_PORT — открой config_local.py в Notepad и поправь руками.
)

echo.
echo [6/7] Auto-generating SECRET_KEY and SUBSCRIPTION_TOKEN (only placeholders)...
".venv\Scripts\python.exe" -c "import re, secrets; p='config_local.py'; s=open(p,encoding='utf-8').read(); changed=False; new=re.sub(r'SECRET_KEY\s*=\s*\"ЗАМЕНИ_МЕНЯ_[^\"]*\"', 'SECRET_KEY = \"'+secrets.token_hex(32)+'\"', s); changed=changed or (new!=s); s=new; new=re.sub(r'SUBSCRIPTION_TOKEN\s*=\s*\"ЗАМЕНИ_МЕНЯ_[^\"]*\"', 'SUBSCRIPTION_TOKEN = \"'+secrets.token_urlsafe(24)+'\"', s); changed=changed or (new!=s); s=new; open(p,'w',encoding='utf-8').write(s); print('Done.' if changed else 'Already set, skipping.')"

REM Читаем итоговый порт
echo.
echo [7/7] Reading final port from config_local.py...
for /f "delims=" %%p in ('".venv\Scripts\python.exe" -c "import config_local as c; print(getattr(c,'DASHBOARD_PORT',5000))" 2^>nul') do set DPORT=%%p
if "%DPORT%"=="" set DPORT=5000
echo Port: %DPORT%

echo.
echo ============================================================
echo  Installation complete!  Port: %DPORT%
echo ============================================================
echo.

findstr /C:"ЗАМЕНИ_МЕНЯ_СВОИМ_ПАРОЛЕМ" config_local.py >nul
if not errorlevel 1 (
    echo  Now I'll open config_local.py in Notepad.
    echo  Change PASSWORD to your own ^(line with PASSWORD = "..."^).
    echo  Если ставишь несколько инстансов — проверь что DASHBOARD_PORT уникален ^(сейчас %DPORT%^).
    echo  Save ^(Ctrl+S^) and close Notepad — then run start.bat.
    echo.
    pause
    notepad config_local.py
    echo.
    echo When you're done editing, run:  .\start.bat ^(или .\start-bg.bat в фоне^)
    echo URL: http://localhost:%DPORT%
    echo.
) else (
    echo  config_local.py уже настроен ^(PASSWORD задан^) — ничего открывать не надо.
    echo  Запускай:  .\start.bat ^(в окне^) или .\start-bg.bat ^(в фоне^)
    echo  URL: http://localhost:%DPORT%
    echo.
)
pause
