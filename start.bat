@echo off
setlocal
title VI Dubber Studio - Server
cd /d "%~dp0"

echo ===================================================
echo           VI DUBBER STUDIO v2.0-PRO
echo    Neural Video Dubbing ^& Acoustic Suite
echo ===================================================
echo.

:: Sanitize NO_PROXY to prevent httpx IPv6 parser issues on Windows
set NO_PROXY=localhost,127.0.0.1
set no_proxy=localhost,127.0.0.1

:: Check if compiled frontend dist exists
if not exist "frontend\dist\index.html" (
    echo [INFO] Compiling frontend assets...
    call cmd /c "cd frontend && npm run build"
)

echo [STARTING] Launching VI Dubber Studio on http://127.0.0.1:7860 ...
echo [INFO] Press Ctrl+C in this console to stop server.
echo.

uv run vi-dubber web %*

if %ERRORLEVEL% neq 0 (
    echo.
    echo [ERROR] Server exited with error code %ERRORLEVEL%.
    pause
)
