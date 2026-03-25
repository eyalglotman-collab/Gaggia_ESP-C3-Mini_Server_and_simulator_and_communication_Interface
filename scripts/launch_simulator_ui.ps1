<#
.SYNOPSIS
Starts the simulator backend and launches the UI in a browser.

.DESCRIPTION
Runs the manual-launch flow inside the single PowerShell session opened by the
batch wrapper, waits for the simulator health endpoint to report ready, and
then opens the simulator in a fresh browser session using a cache-busting URL.

.PARAMETER HostName
Bind address for the simulator backend.

.PARAMETER Port
Bind port for the simulator backend.

.PARAMETER Reload
Enables backend auto-reload for development.

.PARAMETER StartupTimeoutSec
Maximum time to wait for the backend health check to succeed before failing.

.PARAMETER NoBrowser
Skips browser launch after backend startup.
#>
[CmdletBinding()]
param(
    [string]$HostName = '127.0.0.1',
    [int]$Port = 8000,
    [switch]$Reload,
    [int]$StartupTimeoutSec = 20,
    [switch]$NoBrowser
)

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$BackendScript = Join-Path $PSScriptRoot 'run_simulator.ps1'
$VerifierScript = Join-Path $PSScriptRoot 'verify_simulator_installation.ps1'
$HealthUrl = "http://$HostName`:$Port/health"
$UiUrlBase = "http://$HostName`:$Port/"

# @brief Print launch diagnostics to the terminal before showing any popup.
# @details Mirrors popup error content into the terminal so CLI launches always
# include actionable failure details even when message boxes are dismissed.
# @param[in] Title Short failure section title.
# @param[in] Details One-line diagnostic messages to print.
function Write-LaunchDiagnostics {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Title,
        [Parameter(Mandatory = $true)]
        [string[]]$Details
    )

    Write-Host ''
    Write-Host "[$Title]" -ForegroundColor Yellow
    foreach ($line in $Details) {
        Write-Host "  $line" -ForegroundColor Yellow
    }
    Write-Host ''
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

# @brief Open a fresh browser session for the simulator UI.
# @details Uses a cache-busting query string rather than attempting a global
# browser-cache wipe.
# @param[in] UiUrlBase Simulator UI base URL.
function Start-SimulatorBrowser {
    param(
        [Parameter(Mandatory = $true)]
        [string]$UiUrlBase
    )

    $url = $UiUrlBase + '?ts=' + [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
    Write-Host "Opening simulator web client: $url"
    Start-Process $url | Out-Null
}

if (-not (Test-Path $BackendScript)) {
    throw "Backend launcher not found: $BackendScript"
}
if (-not (Test-Path $VerifierScript)) {
    throw "Installation verifier not found: $VerifierScript"
}

. $VerifierScript
. $BackendScript

$requirementsSync = Invoke-SimulatorRequirementsSync
if (-not $requirementsSync.Success) {
    $gaps = New-Object System.Collections.Generic.List[string]
    $gaps.Add("Automatic requirements sync failed: $($requirementsSync.RequirementsFile)")
    $gaps.Add("pip exit code: $($requirementsSync.PipExitCode)")

    if ($requirementsSync.MissingPackages.Count -gt 0) {
        foreach ($packageName in $requirementsSync.MissingPackages) {
            $gaps.Add("Missing package from pip resolution: $packageName")
        }
    }

    $pipTail = @($requirementsSync.Output | Select-Object -Last 8)
    if ($pipTail.Count -gt 0) {
        $gaps.Add('pip output (tail):')
        foreach ($line in $pipTail) {
            $gaps.Add("  $line")
        }
    }

    Write-LaunchDiagnostics -Title 'Simulator Launch Prerequisite Failure' -Details $gaps.ToArray()
    Show-GapMessage -Gaps $gaps.ToArray()
    exit 1
}

$gaps = Get-SimulatorInstallationGaps
if ($gaps.Count -gt 0) {
    Write-LaunchDiagnostics -Title 'Simulator Launch Verification Failure' -Details $gaps
    Show-GapMessage -Gaps $gaps
    exit 1
}

Write-Host "Starting simulator backend on $HostName`:$Port ..."

try {
    $backendProcess = Start-SimulatorBackendProcess -HostName $HostName -Port $Port -Reload $Reload.IsPresent
    Wait-ForBackendHealth -HealthUrl $HealthUrl -StartupTimeoutSec $StartupTimeoutSec
    Write-Host "Simulator backend ready. PID=$($backendProcess.Id)"
} catch {
    $startupErrors = @("Backend startup failed: $($_.Exception.Message)")
    Write-LaunchDiagnostics -Title 'Simulator Launch Runtime Failure' -Details $startupErrors
    Show-GapMessage -Gaps $startupErrors
    exit 1
}

if ($NoBrowser.IsPresent) {
    Write-Host "Backend launched. Browser launch skipped (-NoBrowser)."
    exit 0
}

Start-SimulatorBrowser -UiUrlBase $UiUrlBase
