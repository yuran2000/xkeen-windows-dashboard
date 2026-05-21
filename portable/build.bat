@echo off
REM ======================================================================
REM Build portable exe for xray-dashboard
REM ======================================================================
REM Требует: .venv\Scripts\python.exe в C:\xray-dashboard\
REM Output: portable\dist\xray-dashboard\xray-dashboard.exe
REM ======================================================================

setlocal
cd /d %~dp0..

if not exist .venv\Scripts\python.exe (
    echo ERROR: .venv не найден. Запусти install.bat сначала чтобы создать venv.
    exit /b 1
)

echo [1/3] Installing PyInstaller into venv...
.venv\Scripts\python.exe -m pip install --quiet --upgrade pyinstaller
if errorlevel 1 (
    echo ERROR: pip install pyinstaller failed
    exit /b 1
)

echo [2/3] Cleaning previous build...
if exist portable\build rmdir /s /q portable\build
if exist portable\dist rmdir /s /q portable\dist

echo [3/3] Building (this takes 1-2 minutes)...
set "ROOT=%CD%"
.venv\Scripts\python.exe -m PyInstaller ^
    --noconfirm ^
    --onedir ^
    --name xray-dashboard ^
    --icon "%ROOT%\icons8-favicon-64.png" ^
    --add-data "%ROOT%\icons8-favicon-64.png;." ^
    --add-data "%ROOT%\config_local.example.py;." ^
    --add-data "%ROOT%\portable\config.ini.template;." ^
    --add-data "%ROOT%\bootstrap;bootstrap" ^
    --collect-all qrcode ^
    --collect-all flask ^
    --collect-all jinja2 ^
    --hidden-import psutil ^
    --workpath "%ROOT%\portable\build" ^
    --distpath "%ROOT%\portable\dist" ^
    --specpath "%ROOT%\portable" ^
    "%ROOT%\dashboard.py"

if errorlevel 1 (
    echo.
    echo BUILD FAILED
    exit /b 1
)

echo.
echo ============================================
echo  BUILD OK
echo ============================================
echo  Output: portable\dist\xray-dashboard\xray-dashboard.exe
echo.
echo  Next steps:
echo   1. cd portable\dist\xray-dashboard
echo   2. Copy ..\..\config.ini.template -^> config.ini
echo   3. Edit config.ini (password, secret_key, ssh.host)
echo   4. Copy your id_keenetic SSH key into this folder
echo   5. Run xray-dashboard.exe — UI на http://localhost:5000
echo ============================================
endlocal
