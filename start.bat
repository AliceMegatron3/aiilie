@echo off
echo ===========================================
echo   No.0 AI V4.0 - 全系统一键启动脚本
echo ===========================================

echo [1/2] 正在启动后端总控中枢 (Uvicorn)...
start "No.0 AI V4 Backend" cmd /k "python -m uvicorn main:app --host 127.0.0.1 --port 8000 --reload"

echo [2/2] 正在启动 Electron/Vue 沉浸式前端...
cd frontend
if not exist node_modules (
    echo 初次运行，正在安装前端依赖...
    call npm install
)
start "No.0 AI V4 Frontend" cmd /k "npm run dev"

echo 系统已全量拉起，请查看弹出的终端窗口日志。
exit
