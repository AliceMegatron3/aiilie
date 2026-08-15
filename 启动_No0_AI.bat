@echo off
chcp 65001 >nul
title No.0 AI V4.0 Launcher
color 0b

echo ===========================================
echo       No.0 AI V4.0 - Launcher
echo ===========================================
echo.

REM 1. Start Backend
echo [1/3] Starting Backend (FastAPI / Uvicorn)...
start "No.0 AI Backend" cmd /k "python -m uvicorn main:app --host 127.0.0.1 --port 8000 --reload"

REM 2. Start Frontend
echo [2/3] Starting Frontend (Vue3 / Vite)...
cd frontend
if not exist node_modules (
    echo Installing node modules...
    call npm install
)
start "No.0 AI Frontend" cmd /k "npm run dev"

REM 3. Open Browser
echo [3/3] Opening Browser...
timeout /t 3 >nul
start http://localhost:5173/

echo.
echo ===========================================
echo Started successfully!
echo Frontend: http://localhost:5173/
echo Backend:  http://127.0.0.1:8000/
echo Close the two popup terminals to shut down.
echo ===========================================
pause
