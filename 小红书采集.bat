@echo off
REM XiaoHongShu collect - uses the login saved by ?????.bat
setlocal
cd /d "%~dp0"
set PYEXE=
if exist ".venv\Scripts\python.exe" set PYEXE=.venv\Scripts\python.exe
if "%PYEXE%"=="" (
  for /f "delims=" %%i in ('where python 2^>nul') do (
    if "%PYEXE%"=="" set PYEXE=%%i
  )
)
if "%PYEXE%"=="" (
  echo [ERROR] python not found.
  pause
  exit /b 1
)
echo Collecting XHS notes (vertical keywords only)...
"%PYEXE%" -m app.cli xhs collect %*
echo.
echo Saved to data\xhs_notes.json
pause