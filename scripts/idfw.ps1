<#
.SYNOPSIS
Runs ESP-IDF commands for the simulator bridge firmware from the repository root.

.DESCRIPTION
Resolves the firmware project, default build directory, and effective serial port,
then loads the local ESP-IDF environment helper before forwarding all remaining
arguments to `idf.py`. This keeps firmware commands consistent across shells and
reduces command-line duplication in daily workflow.

.PARAMETER IdfArgs
Remaining `idf.py` arguments to forward after the repository-specific defaults
are applied.
#>
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$IdfArgs
)

$ErrorActionPreference = "Stop"

# @brief Release the COM port held by the simulator around idf actions.
# @details POSTs to the simulator HTTP API to force-release the serial link.
# All errors are suppressed so the command proceeds even when the sim is not running.
function Invoke-SimulatorComRelease {
    if ($env:IDFW_USE_SIM_RELEASE -ne "1") {
        return
    }

    try {
        Invoke-WebRequest -Uri 'http://localhost:8000/api/transport/release-com' `
            -Method Post -TimeoutSec 3 -UseBasicParsing -ErrorAction SilentlyContinue | Out-Null
    } catch { }
    Start-Sleep -Milliseconds 500
}

$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$FirmwareRoot = Join-Path $RepoRoot "firmware\esp32c3_bridge"
$BuildDir = Join-Path $RepoRoot ".idfbuild\esp32c3_bridge"
$DefaultPort = "COM4"

$NormalizedIdfArgs = @()
$HasBuild = $false
$HasFlash = $false
foreach ($arg in $IdfArgs) {
    if ($arg -ieq "flash-only") {
        $NormalizedIdfArgs += "flash"
        $HasFlash = $true
        continue
    }

    $NormalizedIdfArgs += $arg
    if ($arg -ieq "build") {
        $HasBuild = $true
    }
    if ($arg -ieq "flash") {
        $HasFlash = $true
    }
}

if ($HasFlash -and -not $HasBuild) {
    Write-Host "Flash-only mode requested (no build step)."
}

$SelectedPort = $null
for ($i = 0; $i -lt $NormalizedIdfArgs.Count; $i++) {
    if ($NormalizedIdfArgs[$i] -eq '-p' -and ($i + 1) -lt $NormalizedIdfArgs.Count) {
        $SelectedPort = $NormalizedIdfArgs[$i + 1]
        break
    }
}

if (-not $SelectedPort) {
    $SelectedPort = $DefaultPort
}

$env:ESPPORT = $SelectedPort

. (Join-Path $PSScriptRoot "setup_idf_env.ps1") -DefaultPort $SelectedPort

$IdfPath = if ($env:IDF_PATH) { $env:IDF_PATH } else { "C:\Espressif\.espressif\v5.5.2\esp-idf" }
$PythonEnvPath = if ($env:IDF_PYTHON_ENV_PATH) { $env:IDF_PYTHON_ENV_PATH } else { "C:\Espressif\python_env\idf5.5_py3.11_env" }
$PythonExe = Join-Path $PythonEnvPath "Scripts\python.exe"
$IdfPyScript = Join-Path $IdfPath "tools\idf.py"

if (-not (Test-Path $PythonExe)) {
    throw "ESP-IDF Python executable not found at '$PythonExe'."
}

if (-not (Test-Path $IdfPyScript)) {
    throw "idf.py not found at '$IdfPyScript'."
}

$EffectiveArgs = @("-C", $FirmwareRoot)
if (-not ($NormalizedIdfArgs -contains "-B")) {
    $EffectiveArgs += @("-B", $BuildDir)
}
if (-not ($NormalizedIdfArgs -contains "-DIDF_TARGET=esp32c3")) {
    $EffectiveArgs += "-DIDF_TARGET=esp32c3"
}

$EffectiveArgs += $NormalizedIdfArgs

$needsComRelease = ($NormalizedIdfArgs.Count -eq 0) -or ($NormalizedIdfArgs -contains 'build') -or ($NormalizedIdfArgs -contains 'flash') -or ($NormalizedIdfArgs -contains 'monitor')
$exitCode = 1
$invokeException = $null

try {
    if ($needsComRelease) {
        Write-Host "Releasing COM port before idf action..."
        Invoke-SimulatorComRelease
    }

    if ($env:IDFW_DEBUG -eq "1") {
        Write-Host ("IDFW_DEBUG: python={0}" -f $PythonExe)
        Write-Host ("IDFW_DEBUG: idf.py={0}" -f $IdfPyScript)
        Write-Host ("IDFW_DEBUG: args={0}" -f ($EffectiveArgs -join ' '))
    }

    # Launch idf.py through Start-Process to avoid shell-specific cases where
    # external command invocation does not propagate a reliable LASTEXITCODE.
    $pythonArgs = @($IdfPyScript) + $EffectiveArgs
    $idfProcess = Start-Process -FilePath $PythonExe `
        -ArgumentList $pythonArgs `
        -WorkingDirectory $RepoRoot `
        -NoNewWindow `
        -Wait `
        -PassThru

    if ($null -eq $idfProcess -or $null -eq $idfProcess.ExitCode) {
        $exitCode = 1
    } else {
        $exitCode = [int]$idfProcess.ExitCode
    }
} catch {
    $invokeException = $_
    Write-Error ("idfw.ps1 failed to execute idf.py: {0}" -f $_.Exception.Message)
    $exitCode = 1
} finally {
    if ($needsComRelease) {
        Write-Host "Releasing COM port after idf action..."
        Invoke-SimulatorComRelease
    }
}

if ($invokeException -and $env:IDFW_DEBUG -eq "1") {
    Write-Error ("IDFW_DEBUG: stack={0}" -f $invokeException.ScriptStackTrace)
}

exit $exitCode
