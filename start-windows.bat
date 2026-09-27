@echo off
title Isolated Browser Manager
echo Starting Isolated Browser Manager (FastAPI + React)...

cd /d "%~dp0"

REM Check if Python venv exists
if not exist "build\venv_win\Scripts\python.exe" (
    echo Setting up Python virtual environment...
    python -m venv build\venv_win
    build\venv_win\Scripts\python.exe -m pip install -r backend\requirements.txt
)

REM Launch FastAPI backend in background
start "FastAPI Backend" /min build\venv_win\Scripts\python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8000

REM Launch Vite React frontend
cd manager-app
call npm install
call npm run dev
