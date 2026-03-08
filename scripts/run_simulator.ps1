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
    & $PythonExe @args
} finally {
    Pop-Location
}
