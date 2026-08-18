@echo off
setlocal EnableExtensions
chcp 65001 >nul
set "ROOT=%~dp0"
cd /d "%ROOT%"
title No.0 AI V4 Backend

set "PYTHON=python"
if exist "%ROOT%.venv\Scripts\python.exe" set "PYTHON=%ROOT%.venv\Scripts\python.exe"
if not exist "%ROOT%main.py" (
    echo [错误] 未找到 main.py：%ROOT%main.py
    pause
    exit /b 1
)

echo [Backend] Python: %PYTHON%
echo [Backend] Working directory: %CD%
echo [Backend] Starting Uvicorn on http://127.0.0.1:8000 ...
"%PYTHON%" -m uvicorn main:app --host 127.0.0.1 --port 8000 --reload
set "EXIT_CODE=%ERRORLEVEL%"
echo.
echo [Backend] Uvicorn exited with code %EXIT_CODE%.
pause
exit /b %EXIT_CODE%
