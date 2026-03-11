@echo off
setlocal
REM @brief Start the simulator backend through the PowerShell launcher.
REM @details This wrapper gives Windows shells and tools a simple batch entry
REM point while preserving the repo-local PowerShell launch logic in
REM scripts\run_simulator.ps1.
powershell.exe -ExecutionPolicy Bypass -File "%~dp0run_simulator.ps1" %*
set "EXIT_CODE=%ERRORLEVEL%"
endlocal & exit /b %EXIT_CODE%
