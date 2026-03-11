<#
.SYNOPSIS
Starts the FastAPI simulator backend with `uvicorn`.

.DESCRIPTION
Runs the simulator from the repository root, prefers the local virtual
environment interpreter when available, and forwards host/port/reload options
into `uvicorn`. Before launch, the script inspects any existing listener on the
requested port, verifies that it belongs to this simulator backend, and then
tries a graceful stop before falling back to forced termination. This keeps the
launcher safe for developer workflows while still ensuring one active backend
instance per port.

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

# @brief Inspect a process and decide whether it is safe to replace.
# @details Uses process name and command-line evidence to distinguish this
# repository's simulator backend from unrelated listeners that may happen to use
# the same port. The launcher refuses to terminate processes it cannot
# confidently attribute to this simulator.
# @param[in] ProcessId Candidate owning process identifier.
# @return Hashtable with process metadata and a boolean SafeToStop decision.
function Get-ListenerProcessMetadata {
    param(
        [Parameter(Mandatory = $true)]
        [int]$ProcessId
    )

    $process = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
    if (-not $process) {
        return @{
            ProcessId = $ProcessId
            ProcessName = $null
            CommandLine = $null
            SafeToStop = $false
            Reason = "Process no longer exists."
        }
    }

    $commandLine = $null
    try {
        $cimProcess = Get-CimInstance Win32_Process -Filter "ProcessId = $ProcessId" -ErrorAction Stop
        $commandLine = $cimProcess.CommandLine
    } catch {
        $commandLine = $null
    }

    $commandLineLooksSafe = $false
    if ($commandLine) {
        $normalizedProjectRoot = [Regex]::Escape($ProjectRoot)
        $commandLineLooksSafe =
            ($commandLine -match $normalizedProjectRoot) -and
            (
                ($commandLine -match 'run_simulator\.ps1') -or
                ($commandLine -match 'server\.app:app') -or
                ($commandLine -match 'uvicorn')
            )
    }

    $nameLooksSafe = $process.ProcessName -in @('python', 'powershell', 'pwsh')

    return @{
        ProcessId = $ProcessId
        ProcessName = $process.ProcessName
        CommandLine = $commandLine
        SafeToStop = ($nameLooksSafe -and $commandLineLooksSafe)
        Reason = if ($nameLooksSafe -and $commandLineLooksSafe) {
            "Process matches the simulator launcher/backend signature."
        } else {
            "Process could not be positively identified as this simulator backend."
        }
    }
}

# @brief Stop one known simulator listener process with graceful fallback.
# @details Tries a normal stop first so the existing backend can exit cleanly,
# then escalates to forced termination only if the process remains alive past a
# short wait window.
# @param[in] ProcessId Owning process identifier to stop.
function Stop-ListenerProcess {
    param(
        [Parameter(Mandatory = $true)]
        [int]$ProcessId
    )

    $process = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
    if (-not $process) {
        return
    }

    Stop-Process -Id $ProcessId -ErrorAction SilentlyContinue
    Start-Sleep -Milliseconds 750

    $process = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
    if ($process) {
        Stop-Process -Id $ProcessId -Force
    }
}

# @brief Stop any safe-to-replace listener bound to the requested simulator port.
# @details Limits shutdown to `Listen` state endpoints, verifies the owning
# process belongs to this repository's simulator backend, and aborts startup if
# the port is occupied by an unrelated process. This follows the safer pattern
# of identifying the owner before termination instead of killing arbitrary
# listeners by port alone.
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
        $metadata = Get-ListenerProcessMetadata -ProcessId $owningProcess
        if (-not $metadata.SafeToStop) {
            throw "Port $Port is already owned by PID $owningProcess ($($metadata.ProcessName)). $($metadata.Reason)"
        }

        Write-Host "Stopping existing simulator listener PID $owningProcess on port $Port."
        Stop-ListenerProcess -ProcessId $owningProcess
    }

    Start-Sleep -Milliseconds 500
}

# @brief Start the backend simulator with uvicorn.
# @details Runs the FastAPI application from the simulator repository root and
# prefers the local virtual-environment interpreter when available after safely
# clearing any stale simulator listener already bound to the target port.
# @param[in] HostName Bind host for uvicorn.
# @param[in] Port Bind port for uvicorn.
# @param[in] Reload Enable uvicorn auto-reload for development.
Push-Location $ProjectRoot
try {
    # Keep one authoritative backend instance per port without killing unrelated services.
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
