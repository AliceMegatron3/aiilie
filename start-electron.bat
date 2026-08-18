@echo off
setlocal EnableExtensions
chcp 65001 >nul
set "ROOT=%~dp0"
cd /d "%ROOT%"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%ROOT%start-dev.ps1" -Ui electron
if errorlevel 1 (
    echo [错误] Electron 开发启动失败。
    pause
    exit /b 1
)
exit /b 0
