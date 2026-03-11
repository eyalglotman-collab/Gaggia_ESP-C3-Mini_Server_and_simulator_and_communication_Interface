<#
.SYNOPSIS
Starts the simulator backend and offers to open the UI in a browser.

.DESCRIPTION
Launches the canonical backend runner in a dedicated PowerShell host, waits for
the simulator health endpoint to report ready, and then shows a Yes/No message
box asking whether to open the simulator in a fresh browser session using a
cache-busting URL.

.PARAMETER HostName
Bind address for the simulator backend.

.PARAMETER Port
Bind port for the simulator backend.

.PARAMETER Reload
Enables backend auto-reload for development.

.PARAMETER StartupTimeoutSec
Maximum time to wait for the backend health check to succeed before failing.
#>
[CmdletBinding()]
param(
    [string]$HostName = '127.0.0.1',
    [int]$Port = 8000,
    [switch]$Reload,
    [int]$StartupTimeoutSec = 20
)

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$BackendScript = Join-Path $PSScriptRoot 'run_simulator.ps1'
$HealthUrl = "http://$HostName`:$Port/health"
$UiUrlBase = "http://$HostName`:$Port/"

# @brief Start the backend in a dedicated PowerShell host window.
# @details Keeps the long-lived `uvicorn` process outside the short launcher
# session so the backend remains available after the prompt flow returns.
# @param[in] HostName Bind address for the backend.
# @param[in] Port Bind port for the backend.
# @param[in] Reload Enables backend auto-reload.
function Start-BackendHost {
    param(
        [Parameter(Mandatory = $true)]
        [string]$HostName,
        [Parameter(Mandatory = $true)]
        [int]$Port,
        [Parameter(Mandatory = $true)]
        [bool]$Reload
    )

    $argumentList = @(
        '-NoExit',
        '-ExecutionPolicy', 'Bypass',
        '-File', $BackendScript,
        '-HostName', $HostName,
        '-Port', "$Port"
    )
    if ($Reload) {
        $argumentList += '-Reload'
    }

    Start-Process -FilePath powershell.exe -ArgumentList $argumentList -WorkingDirectory $ProjectRoot | Out-Null
}

# @brief Wait for the backend health endpoint to report ready.
# @details Polls `/health` until it returns HTTP 200 or the timeout window
# expires.
# @param[in] HealthUrl Health endpoint to query.
# @param[in] StartupTimeoutSec Maximum wait duration in seconds.
function Wait-ForBackendHealth {
    param(
        [Parameter(Mandatory = $true)]
        [string]$HealthUrl,
        [Parameter(Mandatory = $true)]
        [int]$StartupTimeoutSec
    )

    $deadline = (Get-Date).AddSeconds($StartupTimeoutSec)
    do {
        Start-Sleep -Milliseconds 500
        try {
            $response = Invoke-WebRequest -UseBasicParsing $HealthUrl -TimeoutSec 2
            if ($response.StatusCode -eq 200) {
                return
            }
        } catch {
        }
    } while ((Get-Date) -lt $deadline)

    throw "Simulator backend did not become healthy within $StartupTimeoutSec seconds."
}

# @brief Ask whether to open a fresh browser session for the simulator UI.
# @details Uses a Yes/No Windows message box after backend readiness succeeds.
# The browser launch uses a cache-busting query string rather than attempting a
# global browser-cache wipe.
# @param[in] UiUrlBase Simulator UI base URL.
function Show-BrowserPrompt {
    param(
        [Parameter(Mandatory = $true)]
        [string]$UiUrlBase
    )

    Add-Type -AssemblyName System.Windows.Forms
    $message = "The simulator has loaded.`n`nDo you want to open it in a fresh browser session with a cache-busting URL?"
    $title = "Simulator Loaded"
    $result = [System.Windows.Forms.MessageBox]::Show(
        $message,
        $title,
        [System.Windows.Forms.MessageBoxButtons]::YesNo,
        [System.Windows.Forms.MessageBoxIcon]::Question
    )

    if ($result -eq [System.Windows.Forms.DialogResult]::Yes) {
        $url = $UiUrlBase + '?ts=' + [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
        Start-Process $url | Out-Null
    }
}

if (-not (Test-Path $BackendScript)) {
    throw "Backend launcher not found: $BackendScript"
}

Start-BackendHost -HostName $HostName -Port $Port -Reload $Reload.IsPresent
Wait-ForBackendHealth -HealthUrl $HealthUrl -StartupTimeoutSec $StartupTimeoutSec
Show-BrowserPrompt -UiUrlBase $UiUrlBase
