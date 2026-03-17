<#
.SYNOPSIS
Prepares the local ESP-IDF PowerShell environment for the simulator bridge firmware.

.DESCRIPTION
Uses Espressif's installed `idf-env.exe` configuration plus the stable
`idf_tools.py export --format key-value` path to avoid the newer PowerShell
export flow that is currently failing on this host.

.PARAMETER IdfPath
Absolute ESP-IDF installation path that contains `tools\idf_tools.py`.

.PARAMETER DefaultPort
Serial port to expose through `ESPPORT` when the caller has not already chosen one.

.PARAMETER IdfToolsPath
Absolute Espressif tools root that contains `idf-env.exe`.
#>
param(
    [string]$IdfPath = "C:\Espressif\.espressif\v5.5.2\esp-idf",
    [string]$DefaultPort = "COM4",
    [string]$IdfToolsPath = "C:\Espressif"
)

$ErrorActionPreference = "Stop"

$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$FirmwareRoot = Join-Path $RepoRoot "firmware\esp32c3_bridge"
$CacheDir = Join-Path $RepoRoot ".cache"
$BuildDir = Join-Path $RepoRoot ".idfbuild\esp32c3_bridge"
$IdfToolsPy = Join-Path $IdfPath "tools\idf_tools.py"
$PythonCandidates = @(
    (Join-Path $env:IDF_PYTHON_ENV_PATH "Scripts\python.exe"),
    "C:\Espressif\python_env\idf5.5_py3.11_env\Scripts\python.exe",
    "C:\Espressif\frameworks\esp-idf-v5.5.2\.venv\Scripts\python.exe",
    "C:\Espressif\Eyal_Projects_ESP32_S3\Eyal_espresso_server_simulator\.venv\Scripts\python.exe"
)
$GitCandidates = @(
    ((Get-Command git -ErrorAction SilentlyContinue).Source),
    "C:\Program Files\Git\cmd\git.exe",
    "C:\Program Files\Git\bin\git.exe"
)
$ToolBinCandidates = @()
$ExtraPaths = @(
    (Join-Path $IdfPath "components\espcoredump"),
    (Join-Path $IdfPath "components\partition_table"),
    (Join-Path $IdfPath "components\app_update")
) -join ";"

function Get-LatestToolBinDir {
    param(
        [string]$ToolRoot,
        [string]$ExeName
    )

    if (-not (Test-Path $ToolRoot)) {
        return $null
    }

    $Match = Get-ChildItem -Path $ToolRoot -Recurse -Filter $ExeName -ErrorAction SilentlyContinue |
        Sort-Object FullName -Descending |
        Select-Object -First 1

    if ($null -eq $Match) {
        return $null
    }

    return Split-Path -Parent $Match.FullName
}

if (-not (Test-Path $IdfToolsPy)) {
    throw "ESP-IDF tools script not found at '$IdfToolsPy'. Update -IdfPath."
}

if (-not (Test-Path $FirmwareRoot)) {
    throw "Firmware root not found at '$FirmwareRoot'."
}

New-Item -ItemType Directory -Force -Path $CacheDir | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $CacheDir "Espressif\ComponentManager") | Out-Null
New-Item -ItemType Directory -Force -Path $BuildDir | Out-Null

$env:XDG_CACHE_HOME = $CacheDir
$env:IDF_TOOLS_PATH = $IdfToolsPath
$env:IDF_PATH = $IdfPath
if (-not $env:ESPPORT) {
    $env:ESPPORT = $DefaultPort
}

$PythonCommand = $null
foreach ($candidate in $PythonCandidates) {
    if ($candidate -and (Test-Path $candidate)) {
        $PythonCommand = $candidate
        break
    }
}

if (-not $PythonCommand -or -not (Test-Path $PythonCommand)) {
    throw "Configured ESP-IDF Python executable not found: '$PythonCommand'"
}

$GitCommand = $null
foreach ($candidate in $GitCandidates) {
    if ($candidate -and (Test-Path $candidate)) {
        $GitCommand = $candidate
        break
    }
}

$PythonDir = Split-Path -Parent $PythonCommand
$env:IDF_PYTHON_ENV_PATH = Split-Path -Parent $PythonDir

if ($env:PYTHONPATH) {
    $env:PYTHONPATH = $null
}

if ($env:PYTHONHOME) {
    $env:PYTHONHOME = $null
}

if (-not $env:PYTHONNOUSERSITE) {
    $env:PYTHONNOUSERSITE = "True"
}

if ($GitCommand) {
    $GitDir = Split-Path -Parent $GitCommand
    $GitRoot = Split-Path -Parent $GitDir
    $GitMingwBin = Join-Path $GitRoot "mingw64\bin"
    $GitUsrBin = Join-Path $GitRoot "usr\bin"
    $PathParts = @($PythonDir, $GitDir, $IdfToolsPath)
    if (Test-Path $GitMingwBin) {
        $PathParts += $GitMingwBin
    }
    if (Test-Path $GitUsrBin) {
        $PathParts += $GitUsrBin
    }
    $env:PATH = (($PathParts -join ";") + ";$env:PATH")
} else {
    $env:PATH = "$PythonDir;$IdfToolsPath;$env:PATH"
}

$ToolBinCandidates += Get-LatestToolBinDir -ToolRoot "C:\Espressif\tools\cmake" -ExeName "cmake.exe"
$ToolBinCandidates += Get-LatestToolBinDir -ToolRoot "C:\Espressif\tools\ninja" -ExeName "ninja.exe"
$ToolBinCandidates += Get-LatestToolBinDir -ToolRoot "C:\Espressif\tools\xtensa-esp-elf" -ExeName "xtensa-esp32s3-elf-gcc.exe"
$ToolBinCandidates += Get-LatestToolBinDir -ToolRoot "C:\Espressif\tools\riscv32-esp-elf" -ExeName "riscv32-esp-elf-gcc.exe"
$ToolBinCandidates += Get-LatestToolBinDir -ToolRoot "C:\Espressif\tools\ccache" -ExeName "ccache.exe"

foreach ($toolBin in ($ToolBinCandidates | Where-Object { $_ } | Select-Object -Unique)) {
    $env:PATH = "$toolBin;$env:PATH"
}

$EnvarsRaw = & $PythonCommand $IdfToolsPy export --format key-value --add_paths_extras $ExtraPaths
if ($null -ne $LASTEXITCODE -and $LASTEXITCODE -ne 0) {
    throw "ESP-IDF tools export failed with exit code $LASTEXITCODE."
}

foreach ($line in $EnvarsRaw) {
    if ([string]::IsNullOrWhiteSpace($line)) {
        continue
    }

    $pair = $line -split "=", 2
    if ($pair.Length -ne 2) {
        continue
    }

    Set-Item -Path "Env:$($pair[0].Trim())" -Value $pair[1].Trim()
}

Set-Alias -Name python -Value $PythonCommand -Scope Global

function global:idf.py {
    & $PythonCommand "$IdfPath\tools\idf.py" @args
}

Write-Host "ESP-IDF environment ready"
Write-Host "IDF_PATH=$env:IDF_PATH"
Write-Host "Firmware root: $FirmwareRoot"
Write-Host "Build dir: $BuildDir"
Write-Host "ESPPORT=$env:ESPPORT"
Write-Host "Run: idf.py -C `"$FirmwareRoot`" -B `"$BuildDir`" -DIDF_TARGET=esp32c3 reconfigure"
Write-Host "Run: idf.py -C `"$FirmwareRoot`" -B `"$BuildDir`" build"
Write-Host "Run: idf.py -C `"$FirmwareRoot`" -B `"$BuildDir`" -p COM4 flash"
