@echo off
chcp 65001 > nul
title xray-dashboard service install

echo Запускаю install_service.ps1 в админском PowerShell...
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install_service.ps1"

if errorlevel 1 (
    echo.
    echo [WARN] Скрипт вернул ошибку. Возможно не от администратора.
)
pause
