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

$EffectiveArgs = @("-C", $FirmwareRoot)
if (-not ($IdfArgs -contains "-B")) {
    $EffectiveArgs += @("-B", $BuildDir)
}
if (-not ($IdfArgs -contains "-DIDF_TARGET=esp32c3")) {
    $EffectiveArgs += "-DIDF_TARGET=esp32c3"
}

$EffectiveArgs += $IdfArgs

& idf.py @EffectiveArgs
exit $LASTEXITCODE
