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

$SelectedPort = $null
for ($i = 0; $i -lt $IdfArgs.Count; $i++) {
    if ($IdfArgs[$i] -eq '-p' -and ($i + 1) -lt $IdfArgs.Count) {
        $SelectedPort = $IdfArgs[$i + 1]
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
if (-not ($IdfArgs -contains "-B")) {
    $EffectiveArgs += @("-B", $BuildDir)
}
if (-not ($IdfArgs -contains "-DIDF_TARGET=esp32c3")) {
    $EffectiveArgs += "-DIDF_TARGET=esp32c3"
}

$EffectiveArgs += $IdfArgs

$needsComRelease = ($IdfArgs.Count -eq 0) -or ($IdfArgs -contains 'build') -or ($IdfArgs -contains 'flash') -or ($IdfArgs -contains 'monitor')
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

    & $PythonExe $IdfPyScript @EffectiveArgs
    if ($null -eq $LASTEXITCODE) {
        $exitCode = 1
    } else {
        $exitCode = $LASTEXITCODE
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
