@echo off
rem ===========================================================================
rem  GD Live Calendar - one-click launcher (Chinese-named entry)
rem
rem  Thin wrapper around start.bat; use either file name.
rem  Arguments are passed through unchanged - see start.bat for options.
rem
rem  NOTE: keep this file pure ASCII. cmd.exe decodes .bat as ANSI/GBK, so
rem        Chinese text here would be mangled into stray commands.
rem ===========================================================================

call "%~dp0start.bat" %*
exit /b %ERRORLEVEL%
