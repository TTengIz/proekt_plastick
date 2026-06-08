@echo off
chcp 65001 >nul
title Учёт смен - Запуск

cd /d "%~dp0"

if exist ".venv\Scripts\activate.bat" (
    call ".venv\Scripts\activate.bat"
)

echo ========================================
echo   Запуск системы учёта смен
echo ========================================
echo.
echo [1/2] Запуск сервера...
start /B python -m uvicorn main:app --host 127.0.0.1 --port 8000 --log-level warning
timeout /t 3 /nobreak >nul

echo [2/2] Запуск приложения...
python desktop_app.py

echo.
echo Остановка сервера...
taskkill /F /FI "WINDOWTITLE eq uvicorn*" 2>nul
pause