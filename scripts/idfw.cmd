@echo off
setlocal

set "SCRIPT_DIR=%~dp0"
set "PS_WRAPPER=%SCRIPT_DIR%idfw.ps1"

if not exist "%PS_WRAPPER%" (
    echo Wrapper script not found at "%PS_WRAPPER%"
    exit /b 1
)

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%PS_WRAPPER%" %*
set "CMD_EXIT=%ERRORLEVEL%"

exit /b %CMD_EXIT%
