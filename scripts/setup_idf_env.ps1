<#
.SYNOPSIS
Prepares the local ESP-IDF PowerShell environment for the simulator bridge firmware.

.DESCRIPTION
Validates the ESP-IDF export script path, ensures the repository cache and build
directories exist, sets the default serial port when needed, and imports the
ESP-IDF environment so later `idf.py` commands use consistent local paths.

.PARAMETER IdfPath
Absolute ESP-IDF installation path that contains `export.ps1`.

.PARAMETER DefaultPort
Serial port to expose through `ESPPORT` when the caller has not already chosen one.
#>
param(
    [string]$IdfPath = "C:\Espressif\.espressif\v5.5.2\esp-idf",
    [string]$DefaultPort = "COM4"
)

$ErrorActionPreference = "Stop"

$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$FirmwareRoot = Join-Path $RepoRoot "firmware\esp32c3_bridge"
$CacheDir = Join-Path $RepoRoot ".cache"
$BuildDir = Join-Path $RepoRoot ".idfbuild\esp32c3_bridge"
$ExportScript = Join-Path $IdfPath "export.ps1"

if (-not (Test-Path $ExportScript)) {
    throw "ESP-IDF export script not found at '$ExportScript'. Update -IdfPath."
}

if (-not (Test-Path $FirmwareRoot)) {
    throw "Firmware root not found at '$FirmwareRoot'."
}

New-Item -ItemType Directory -Force -Path $CacheDir | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $CacheDir "Espressif\ComponentManager") | Out-Null
New-Item -ItemType Directory -Force -Path $BuildDir | Out-Null

$env:XDG_CACHE_HOME = $CacheDir
if (-not $env:ESPPORT) {
    $env:ESPPORT = $DefaultPort
}

# Import the ESP-IDF shell exports only after the repo-local paths are ready.
. $ExportScript

Write-Host "ESP-IDF environment ready"
Write-Host "IDF_PATH=$env:IDF_PATH"
Write-Host "Firmware root: $FirmwareRoot"
Write-Host "Build dir: $BuildDir"
Write-Host "ESPPORT=$env:ESPPORT"
Write-Host "Run: idf.py -C `"$FirmwareRoot`" -B `"$BuildDir`" -DIDF_TARGET=esp32c3 reconfigure"
Write-Host "Run: idf.py -C `"$FirmwareRoot`" -B `"$BuildDir`" build"
Write-Host "Run: idf.py -C `"$FirmwareRoot`" -B `"$BuildDir`" -p COM4 flash"
