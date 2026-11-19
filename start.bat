@echo off
title O.L.E.G. Launcher

echo ============================================================
echo   Starting O.L.E.G. AI Assistant
echo ============================================================

set "PATH=%LOCALAPPDATA%\Programs\Python\Python314;%LOCALAPPDATA%\Programs\Python\Python314\Scripts;%LOCALAPPDATA%\Programs\Python\Launcher;C:\Program Files\nodejs;%APPDATA%\npm;%PATH%"

set "PY_CMD="
if exist "%~dp0backend\venv\Scripts\python.exe" (
    set "PY_CMD=%~dp0backend\venv\Scripts\python.exe"
) else if exist "%LOCALAPPDATA%\Programs\Python\Python314\python.exe" (
    set "PY_CMD=%LOCALAPPDATA%\Programs\Python\Python314\python.exe"
) else (
    set "PY_CMD=python"
)

echo Checking and closing previous O.L.E.G. instances...
for /f "tokens=5" %%a in ('netstat -aon ^| findstr ":8000" ^| findstr "LISTENING"') do (
    taskkill /F /PID %%a 2>nul
)
for /f "tokens=5" %%a in ('netstat -aon ^| findstr ":8001" ^| findstr "LISTENING"') do (
    taskkill /F /PID %%a 2>nul
)

echo [1/2] Starting FastAPI Backend...
start "OLEG Backend Logs" cmd /k "chcp 65001 >nul && title OLEG Backend Logs && cd /d %~dp0backend && "%PY_CMD%" main.py"

echo Waiting for backend to initialize...
ping 127.0.0.1 -n 4 >nul

echo [2/2] Starting Electron UI...
start "OLEG UI" cmd /c "chcp 65001 >nul && title OLEG UI && cd /d %~dp0electron && npm start"

echo ============================================================
echo   [OK] O.L.E.G. is running!
echo   Press Ctrl + Space to toggle O.L.E.G.
echo ============================================================
