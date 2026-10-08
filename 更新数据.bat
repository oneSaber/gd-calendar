@echo off
rem ===========================================================================
rem  GD Live Calendar - update data only (does NOT start or stop the service)
rem
rem  Double-click this file to re-crawl the sources. Good for Task Scheduler.
rem
rem  Two modes, picked automatically:
rem    * service is running  -> calls its job API, shows live progress,
rem                             and the web page's Update button sees it too
rem    * service is not up   -> runs "python -m app.cli fetch" directly
rem
rem  Options (use from a command prompt):
rem    update.bat -Sources showstart,douban,bilibili,weibo -UseBrowser
rem    update.bat -City GZ,SZ            only these cities
rem    update.bat -Backfill              recompute band/idol/ACG flags
rem    update.bat -DryRun                show what would happen, change nothing
rem    update.bat -AutoInstall           install deps without asking
rem
rem  NOTE: keep this file pure ASCII. cmd.exe decodes .bat as ANSI/GBK, so any
rem        Chinese text here would be mangled into stray commands. All Chinese
rem        UI output is produced by scripts\update.ps1 (UTF-8 with BOM) instead.
rem ===========================================================================

setlocal
chcp 65001 >nul 2>&1
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"

set "SCRIPT=%~dp0scripts\update.ps1"
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

rem keep the window open when double-clicked so the summary stays readable
if "%~1"=="" (
    echo.
    pause
)

endlocal & exit /b %RC%
