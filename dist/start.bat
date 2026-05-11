@echo off
chcp 65001 >nul
title Учёт смен - Сервер

echo ========================================
echo   Запуск системы учёта смен
echo ========================================
echo.

cd /d "%~dp0"

if exist "..\\.venv\\Scripts\\activate.bat" (
    echo Активация виртуального окружения...
    call "..\\.venv\\Scripts\\activate.bat"
) else (
    echo Предупреждение: venv не найден
)

echo.
echo Запуск сервера...
start /B python -m uvicorn main:app --host 127.0.0.1 --port 8000 --log-level warning

echo Ожидание запуска сервера (5 секунд)...
timeout /t 5 /nobreak >nul

echo.
echo Запуск приложения...
ShiftApp.exe

echo.
echo Остановка сервера...
taskkill /F /FI "WINDOWTITLE eq uvicorn*" /FI "IMAGENAME eq python.exe" 2>nul

pause