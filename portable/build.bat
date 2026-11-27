@echo off
setlocal
cd /d "%~dp0.."
set "PYTHON=.venv\Scripts\python.exe"
if not exist "%PYTHON%" (
    echo ERROR: run install.bat first to create the Python environment.
    exit /b 1
)
"%PYTHON%" -m pip install --quiet "pyinstaller==6.17.0"
if errorlevel 1 exit /b 1
"%PYTHON%" tools\build_portable_release.py --output portable
exit /b %ERRORLEVEL%
