@echo off
title Stop O.L.E.G.
echo Stopping O.L.E.G. processes...

:: Stop FastAPI backend on port 8000
for /f "tokens=5" %%a in ('netstat -aon ^| findstr ":8000" ^| findstr "LISTENING"') do (
    taskkill /F /PID %%a 2>nul
)

:: Stop Electron process on port 8001
for /f "tokens=5" %%a in ('netstat -aon ^| findstr ":8001" ^| findstr "LISTENING"') do (
    taskkill /F /PID %%a 2>nul
)

echo [OK] O.L.E.G. stopped.
ping 127.0.0.1 -n 2 >nul
