@echo off
title Stop Isolated Browser Manager
echo ========================================================
echo   Stopping Isolated Browser Manager in WSL2...
echo ========================================================
echo.

wsl.exe -d Ubuntu -u root -- bash -c "pkill -f 'uvicorn backend.main' || true; pkill -f 'vite' || true; pkill -f 'concurrently' || true"
echo [*] Stopped background processes.
ping -n 3 127.0.0.1 >nul
