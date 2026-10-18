@echo off
setlocal
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1" %*
set "narrative_exit_code=%errorlevel%"
if not "%narrative_exit_code%"=="0" (
    echo.
    echo Startup failed. See the message above.
    pause
)
exit /b %narrative_exit_code%
