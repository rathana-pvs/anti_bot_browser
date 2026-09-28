@echo off
title Isolated Browser Manager (WSL2 + Docker)
echo ========================================================
echo   Isolated Browser Manager - Anti-Bot Framework
echo   Mode: Option A (WSL2 + Docker Container Isolation)
echo ========================================================
echo.

cd /d "%~dp0"

REM 1. Ensure Docker is running in Ubuntu WSL2
echo [*] Checking Docker daemon in WSL2 (Ubuntu)...
wsl.exe -d Ubuntu -u root -- bash -c "service docker status >/dev/null 2>&1 || service docker start"

REM 2. Synchronize workspace files to WSL
echo [*] Synchronizing latest workspace changes to WSL...
wsl.exe -d Ubuntu -- bash -c "mkdir -p /root/automat_fb-beta && rsync -au --exclude 'node_modules' --exclude 'build' --exclude '.venv' --exclude 'venv' --exclude '.git' --exclude '__pycache__' --exclude '*.pyc' /mnt/c/Users/admin/Desktop/anti_bot_browser/. /root/automat_fb-beta/"

REM 3. Stop any previous instances to prevent port collisions
wsl.exe -d Ubuntu -u root -- bash -c "pkill -f 'uvicorn backend.main' || true; pkill -f 'vite' || true; pkill -f 'concurrently' || true" >nul 2>&1

REM 4. Open Dashboard in browser after a short delay
start "" cmd /c "ping -n 4 127.0.0.1 >nul & start http://localhost:5173"

REM 5. Start the Application (FastAPI :8000 + Vite :5173)
echo [*] Launching FastAPI Backend (:8000) and Vite Dashboard (:5173)...
echo [*] Open http://localhost:5173 to view the dashboard.
echo.
wsl.exe -d Ubuntu -- bash -lc "cd ~/automat_fb-beta/manager-app && npm start"
