@echo off
setlocal
set SCRIPT_DIR=%~dp0
powershell -ExecutionPolicy Bypass -NoProfile -File "%SCRIPT_DIR%idfw.ps1" %*
exit /b %ERRORLEVEL%