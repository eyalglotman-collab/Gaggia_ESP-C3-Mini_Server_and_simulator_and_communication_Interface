[CmdletBinding()]
param(
    [string]$Port = "COM4",
    [switch]$BuildFirst
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$BuildDir = Join-Path $ProjectRoot ".idfbuild\esp32c3_bridge"
$OutLog = Join-Path $BuildDir "bg_flash.out.log"
$ErrLog = Join-Path $BuildDir "bg_flash.err.log"
$IdfwScript = Join-Path $PSScriptRoot "idfw.ps1"
$SuccessSoundScript = Join-Path $PSScriptRoot "play_build_success_sound.ps1"

New-Item -ItemType Directory -Force -Path $BuildDir | Out-Null
if (Test-Path $OutLog) { Remove-Item $OutLog -Force }
if (Test-Path $ErrLog) { Remove-Item $ErrLog -Force }

$args = @(
    "-NoProfile",
    "-ExecutionPolicy", "Bypass",
    "-File", $IdfwScript
)

if ($BuildFirst) {
    $args += "build"
}

$args += @("-p", $Port, "flash")

$proc = Start-Process -FilePath "powershell.exe" `
    -WindowStyle Hidden `
    -ArgumentList $args `
    -RedirectStandardOutput $OutLog `
    -RedirectStandardError $ErrLog `
    -PassThru `
    -Wait

$proc.Refresh()
$exitCode = $proc.ExitCode

if ($exitCode -eq 0 -and (Test-Path $SuccessSoundScript)) {
    try {
        Start-Process -FilePath "powershell.exe" `
            -WindowStyle Hidden `
            -ArgumentList @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $SuccessSoundScript) `
            -PassThru | Out-Null
    } catch {
        Write-Warning ("Success sound failed: {0}" -f $_.Exception.Message)
    }
}

Write-Host ("Flash exit code: {0}" -f $exitCode)
Write-Host ("Output log: {0}" -f $OutLog)
Write-Host ("Error log: {0}" -f $ErrLog)
exit $exitCode
