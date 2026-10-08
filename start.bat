@echo off
rem ===========================================================================
rem  GD Live Calendar - one-click launcher (local API + web UI)
rem
rem  Double-click this file: it checks the environment, installs deps if needed,
rem  creates the database, starts the service and opens the browser.
rem
rem  Options (use from a command prompt):
rem    start.bat -Port 8010                 use another port
rem    start.bat -Fetch                     force one crawl round
rem    start.bat -Fetch -City GZ,SZ         specify cities
rem    start.bat -NoBrowser                 do not open the browser
rem    start.bat -AutoInstall               install deps without asking
rem
rem  Full data (adds browser sources Bilibili/Weibo, needs Edge or Chrome):
rem    start.bat -Fetch -Sources showstart,douban,bilibili,weibo
rem
rem  NOTE: keep this file pure ASCII. cmd.exe decodes .bat as ANSI/GBK, so any
rem        Chinese text here would be mangled into stray commands. All Chinese
rem        UI output is produced by scripts\start.ps1 (UTF-8 with BOM) instead.
rem ===========================================================================

setlocal
chcp 65001 >nul 2>&1
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"

set "SCRIPT=%~dp0scripts\start.ps1"
if not exist "%SCRIPT%" (
    echo [ERROR] not found: %SCRIPT%
    echo         please run this file from the project root.
    pause
    exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT%" %*
set "RC=%ERRORLEVEL%"

if not "%RC%"=="0" (
    echo.
    echo [FAILED] exit code = %RC%
    pause
)
endlocal & exit /b %RC%
