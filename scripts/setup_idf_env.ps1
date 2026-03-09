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

. $ExportScript

Write-Host "ESP-IDF environment ready"
Write-Host "IDF_PATH=$env:IDF_PATH"
Write-Host "Firmware root: $FirmwareRoot"
Write-Host "Build dir: $BuildDir"
Write-Host "ESPPORT=$env:ESPPORT"
Write-Host "Run: idf.py -C `"$FirmwareRoot`" -B `"$BuildDir`" -DIDF_TARGET=esp32c3 reconfigure"
Write-Host "Run: idf.py -C `"$FirmwareRoot`" -B `"$BuildDir`" build"
Write-Host "Run: idf.py -C `"$FirmwareRoot`" -B `"$BuildDir`" -p COM4 flash"