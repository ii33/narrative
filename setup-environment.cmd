@echo off
setlocal
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup-environment.ps1"
set "narrative_exit_code=%errorlevel%"
if not "%narrative_exit_code%"=="0" (
    echo.
    echo Environment setup failed. See the message above.
) else (
    echo.
    echo Environment setup completed.
)
pause
exit /b %narrative_exit_code%
