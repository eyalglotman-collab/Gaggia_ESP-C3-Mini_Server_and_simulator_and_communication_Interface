@echo off
setlocal
REM @brief Start the simulator backend through the PowerShell launcher.
REM @details This wrapper gives Windows shells and tools a simple batch entry
REM point for the manual launcher flow. It hands off directly into one visible
REM PowerShell session, waits for health, and then asks whether to open the UI
REM in a fresh browser session.
powershell.exe -NoExit -ExecutionPolicy Bypass -File "%~dp0launch_simulator_ui.ps1" %*
set "EXIT_CODE=%ERRORLEVEL%"
endlocal & exit /b %EXIT_CODE%
