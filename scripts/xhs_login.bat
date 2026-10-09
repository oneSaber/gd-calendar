@echo off
REM XiaoHongShu login helper: opens a visible browser for QR scan.
REM ASCII only - Windows console is GBK and would mangle Chinese text.
setlocal
cd /d "%~dp0.."

set PYEXE=
if exist ".venv\Scripts\python.exe" set PYEXE=.venv\Scripts\python.exe
if "%PYEXE%"=="" (
  for /f "delims=" %%i in ('where python 2^>nul') do (
    if "%PYEXE%"=="" set PYEXE=%%i
  )
)
if "%PYEXE%"=="" (
  echo [ERROR] python not found. Run the main launcher first.
  pause
  exit /b 1
)

echo ============================================================
echo  XiaoHongShu (XHS) collection - needs your own login
echo ============================================================
echo.
echo  Step 1: a visible browser window will open.
echo          Scan the QR code with the XHS app on your phone.
echo  Step 2: come back here and press any key to check status.
echo.
echo  NOTE: creator-center login alone is NOT enough -
echo        the main site (www.xiaohongshu.com) needs its own session.
echo.

echo [1/3] Launching browser (persistent profile)...
"%PYEXE%" scripts\xhs_publish.py launch

echo.
echo [2/3] Opening login page...
start "" "http://127.0.0.1:9222/json/version" >nul 2>nul
timeout /t 3 /nobreak >nul

echo.
echo [3/3] Waiting for you to scan...
echo       (this will poll for up to 4 minutes)
echo.
"%PYEXE%" -m app.cli xhs login

echo.
echo ============================================================
echo  After login, collect with:
echo    "%PYEXE%" -m app.cli xhs collect
echo  Or double-click the collect .bat in the project root.
echo ============================================================
pause
