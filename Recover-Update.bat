@echo off
setlocal EnableExtensions DisableDelayedExpansion
cd /d "%~dp0"
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File "%~dp0Recover-Update.ps1"
set "recover_status=%errorlevel%"
pause
exit /b %recover_status%
