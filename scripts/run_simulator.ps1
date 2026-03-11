<#
.SYNOPSIS
Starts the FastAPI simulator backend with `uvicorn`.

.DESCRIPTION
Runs the simulator from the repository root, prefers the local virtual
environment interpreter when available, and forwards host/port/reload options
into `uvicorn`. This script is the canonical repo-local backend launcher for
development and operator testing.

.PARAMETER HostName
Bind address for the `uvicorn` server.

.PARAMETER Port
Bind port for the `uvicorn` server.

.PARAMETER Reload
Enables `uvicorn --reload` for development sessions.
#>
[CmdletBinding()]
param(
    [string]$HostName = '127.0.0.1',
    [int]$Port = 8000,
    [switch]$Reload
)

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$LocalPython = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
$PythonExe = if (Test-Path $LocalPython) { $LocalPython } else { 'python' }

# @brief Start the backend simulator with uvicorn.
# @details Runs the FastAPI application from the simulator repository root and
# prefers the local virtual-environment interpreter when available.
# @param[in] HostName Bind host for uvicorn.
# @param[in] Port Bind port for uvicorn.
# @param[in] Reload Enable uvicorn auto-reload for development.
Push-Location $ProjectRoot
try {
    $args = @('-m', 'uvicorn', 'server.app:app', '--host', $HostName, '--port', "$Port")
    if ($Reload) {
        $args += '--reload'
    }
    # Execute from the repo root so relative paths inside the app resolve consistently.
    & $PythonExe @args
} finally {
    Pop-Location
}
