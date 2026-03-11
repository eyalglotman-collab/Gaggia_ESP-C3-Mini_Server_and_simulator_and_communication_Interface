@echo off
setlocal
REM @brief Start the simulator backend through the PowerShell launcher.
REM @details This wrapper gives Windows shells and tools a simple batch entry
REM point for the manual launcher flow. It starts the backend through the
REM repo-local PowerShell helper, waits for health, and then asks whether to
REM open the UI in a fresh browser session.
powershell.exe -ExecutionPolicy Bypass -File "%~dp0verify_simulator_installation.ps1"
if errorlevel 1 (
    set "EXIT_CODE=%ERRORLEVEL%"
    endlocal & exit /b %EXIT_CODE%
)

powershell.exe -ExecutionPolicy Bypass -File "%~dp0launch_simulator_ui.ps1" %*
set "EXIT_CODE=%ERRORLEVEL%"
endlocal & exit /b %EXIT_CODE%
