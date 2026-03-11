<#
.SYNOPSIS
Starts the simulator backend and offers to open the UI in a browser.

.DESCRIPTION
Runs the manual-launch flow inside the single PowerShell session opened by the
batch wrapper, waits for the simulator health endpoint to report ready, and
then shows a Yes/No message box asking whether to open the simulator in a fresh
browser session using a cache-busting URL.

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
$VerifierScript = Join-Path $PSScriptRoot 'verify_simulator_installation.ps1'
$HealthUrl = "http://$HostName`:$Port/health"
$UiUrlBase = "http://$HostName`:$Port/"

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
if (-not (Test-Path $VerifierScript)) {
    throw "Installation verifier not found: $VerifierScript"
}

. $VerifierScript
. $BackendScript

$gaps = Get-SimulatorInstallationGaps
if ($gaps.Count -gt 0) {
    Show-GapMessage -Gaps $gaps
    exit 1
}

Write-Host "Starting simulator backend on $HostName`:$Port ..."
$backendProcess = Start-SimulatorBackendProcess -HostName $HostName -Port $Port -Reload $Reload.IsPresent
Wait-ForBackendHealth -HealthUrl $HealthUrl -StartupTimeoutSec $StartupTimeoutSec
Write-Host "Simulator backend ready. PID=$($backendProcess.Id)"
Show-BrowserPrompt -UiUrlBase $UiUrlBase
