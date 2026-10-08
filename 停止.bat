@echo off
rem ===========================================================================
rem  GD Live Calendar - stop the local service
rem
rem  Usage:
rem    stop.bat              stop the service on the default port (8000)
rem    stop.bat -Port 8010   stop the service on another port
rem
rem  Only stops this project's uvicorn process (the command line is verified),
rem  so other programs on the same port are left alone.
rem
rem  NOTE: keep this file pure ASCII - cmd.exe decodes .bat as ANSI/GBK.
rem ===========================================================================

setlocal
chcp 65001 >nul 2>&1
set "PYTHONIOENCODING=utf-8"

set "SCRIPT=%~dp0scripts\stop.ps1"
if not exist "%SCRIPT%" (
    echo [ERROR] not found: %SCRIPT%
    exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT%" %*
set "RC=%ERRORLEVEL%"
endlocal & exit /b %RC%
