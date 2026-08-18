@echo off
setlocal EnableExtensions
chcp 65001 >nul
set "ROOT=%~dp0"
cd /d "%ROOT%frontend"
title No.0 AI V4 Frontend

if not exist "%ROOT%frontend\package.json" (
    echo [错误] 未找到 frontend\package.json
    pause
    exit /b 1
)
if not exist "%ROOT%frontend\node_modules" (
    echo [提示] 前端依赖目录不存在，正在安装依赖...
    call npm install
    if errorlevel 1 (
        echo [错误] npm install 失败。
        pause
        exit /b 1
    )
)

echo [Frontend] Working directory: %CD%
echo [Frontend] Starting Vite on http://127.0.0.1:5173 ...
call npm run dev -- --host 127.0.0.1
set "EXIT_CODE=%ERRORLEVEL%"
echo.
echo [Frontend] Vite exited with code %EXIT_CODE%.
pause
exit /b %EXIT_CODE%
