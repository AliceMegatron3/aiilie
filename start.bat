@echo off
setlocal EnableExtensions
chcp 65001 >nul
set "ROOT=%~dp0"
cd /d "%ROOT%"
title No.0 AI V4.0 Launcher

echo ===========================================
echo   No.0 AI V4.0 - 全系统一键启动脚本
echo ===========================================
echo.

if not exist "%ROOT%main.py" (
    echo [错误] 未找到 main.py：%ROOT%main.py
    pause
    exit /b 1
)
if not exist "%ROOT%frontend\package.json" (
    echo [错误] 未找到 frontend\package.json
    pause
    exit /b 1
)
where npm >nul 2>&1
if errorlevel 1 (
    echo [错误] 未找到 npm，请先安装 Node.js 18 或更高版本。
    pause
    exit /b 1
)

set "PYTHON=python"
if exist "%ROOT%.venv\Scripts\python.exe" set "PYTHON=%ROOT%.venv\Scripts\python.exe"
"%PYTHON%" -c "import uvicorn" >nul 2>&1
if errorlevel 1 (
    echo [错误] 当前 Python 环境缺少 uvicorn：%PYTHON%
    echo 请先运行：uv sync --frozen
    pause
    exit /b 1
)

if not exist "%ROOT%frontend\node_modules" (
    echo [提示] 前端依赖目录不存在，正在安装依赖...
    pushd "%ROOT%frontend"
    call npm install
    if errorlevel 1 (
        popd
        echo [错误] 前端依赖安装失败，请查看上方日志。
        pause
        exit /b 1
    )
    popd
)

echo [1/2] 正在启动后端和前端...
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%ROOT%start-dev.ps1" -Ui browser
if errorlevel 1 (
    echo [错误] PowerShell 启动器失败，请直接运行 start-dev.ps1 查看详细错误。
    pause
    exit /b 1
)

echo.
echo 启动窗口已打开，请查看两个窗口中的实时日志。
echo 后端：http://127.0.0.1:8000/health
echo 前端：http://127.0.0.1:5173/
exit /b 0
