@echo off
setlocal
REM @brief Start the simulator backend through the PowerShell launcher.
REM @details This wrapper gives Windows shells and tools a simple batch entry
REM point for the manual launcher flow. It opens one temporary PowerShell
REM session, waits for health, prompts for the browser, and then exits so only
REM the backend host remains visible.
powershell.exe -ExecutionPolicy Bypass -File "%~dp0launch_simulator_ui.ps1" %*
set "EXIT_CODE=%ERRORLEVEL%"
endlocal & exit /b %EXIT_CODE%
