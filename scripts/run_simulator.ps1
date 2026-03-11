<#
.SYNOPSIS
Starts the FastAPI simulator backend with `uvicorn`.

.DESCRIPTION
Runs the simulator from the repository root, prefers the local virtual
environment interpreter when available, and forwards host/port/reload options
into `uvicorn`. Before launch, the script stops any existing listener already
bound to the requested port so operator testing always starts from one active
backend instance. This script is the canonical repo-local backend launcher for
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

# @brief Stop any existing listener bound to the requested simulator port.
# @details Finds listening TCP endpoints for the target port and force-stops the
# owning processes so the next `uvicorn` launch starts from a clean single-backend
# baseline. Duplicate owning PIDs are de-duplicated before termination.
# @param[in] Port TCP port that the new simulator instance will bind.
function Stop-PortListeners {
    param(
        [Parameter(Mandatory = $true)]
        [int]$Port
    )

    $listeners = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    if (-not $listeners) {
        return
    }

    $owningProcesses = $listeners |
        Select-Object -ExpandProperty OwningProcess -Unique |
        Where-Object { $_ -and $_ -ne 0 }

    foreach ($owningProcess in $owningProcesses) {
        $process = Get-Process -Id $owningProcess -ErrorAction SilentlyContinue
        if (-not $process) {
            continue
        }

        Write-Host "Stopping existing listener PID $owningProcess on port $Port."
        Stop-Process -Id $owningProcess -Force
    }

    Start-Sleep -Milliseconds 500
}

# @brief Start the backend simulator with uvicorn.
# @details Runs the FastAPI application from the simulator repository root and
# prefers the local virtual-environment interpreter when available after clearing
# any stale listener already bound to the target port.
# @param[in] HostName Bind host for uvicorn.
# @param[in] Port Bind port for uvicorn.
# @param[in] Reload Enable uvicorn auto-reload for development.
Push-Location $ProjectRoot
try {
    # Keep one authoritative backend instance per port so operator testing never hits a stale listener.
    Stop-PortListeners -Port $Port
    $args = @('-m', 'uvicorn', 'server.app:app', '--host', $HostName, '--port', "$Port")
    if ($Reload) {
        $args += '--reload'
    }
    # Execute from the repo root so relative paths inside the app resolve consistently.
    & $PythonExe @args
} finally {
    Pop-Location
}
