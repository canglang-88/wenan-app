@echo off
setlocal

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0create_shortcut.ps1"

if errorlevel 1 (
    echo Create shortcut failed. Please right-click this file and run as administrator.
    pause
    exit /b 1
)

echo.
echo Shortcut created. You can open Wenan App from the desktop.
pause
