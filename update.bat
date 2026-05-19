@echo off
chcp 65001 > nul
title xray-dashboard update

REM ============================================================
REM Wrapper для update.ps1. ВСЯ логика — там.
REM
REM Почему так:
REM   Старый update.bat сам делал git pull + restart. Проблема: cmd.exe
REM   читает .bat построчно с диска. После git pull файл меняется на диске
REM   — cmd теряет offset и читает мусор ('requirements.txt' is not
REM   recognized as command). PowerShell же загружает весь .ps1 в память
REM   при старте, поэтому git pull внутри .ps1 ничего не ломает.
REM
REM Этот .bat выполняет максимум 2 действия:
REM   1. Если задача XrayDashboard зарегистрирована — запросить UAC (нужен
REM      админ для schtasks /End и /Run).
REM   2. Запустить powershell -File update.ps1.
REM ============================================================

cd /d "%~dp0"

REM Читаем DASHBOARD_PORT из config_local.py (default 5000)
set DPORT=5000
if exist "config_local.py" if exist ".venv\Scripts\python.exe" (
    for /f "delims=" %%p in ('".venv\Scripts\python.exe" -c "import config_local as c; print(getattr(c,'DASHBOARD_PORT',5000))" 2^>nul') do set DPORT=%%p
)
if "%DPORT%"=="" set DPORT=5000

REM Имя Task Scheduler-задачи
set TASKNAME=XrayDashboard
if not "%DPORT%"=="5000" set TASKNAME=XrayDashboard-%DPORT%

REM Если задача существует — нужны admin-права (для schtasks /End и /Run).
REM Self-elevate через UAC если ещё не от админа.
schtasks /query /TN "%TASKNAME%" >nul 2>&1
if errorlevel 1 goto :run_ps

NET FILE 1>NUL 2>NUL
if '%errorlevel%' == '0' goto :run_ps

echo  Нужны admin-права для управления Task Scheduler-задачей "%TASKNAME%".
echo  Запрашиваю UAC...
powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
exit /b

:run_ps
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0update.ps1"
