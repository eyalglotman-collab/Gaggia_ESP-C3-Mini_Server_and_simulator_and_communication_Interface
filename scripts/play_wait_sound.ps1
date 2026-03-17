<#
.SYNOPSIS
Plays one notification sound synchronously.

.DESCRIPTION
Attempts several Windows playback backends in order so repository sound cues
remain usable across different host audio configurations. If file playback
fails, the script falls back to a console beep before raising an error.

.PARAMETER SoundFile
Absolute or relative path to the sound file that should be played.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$SoundFile,
    [switch]$Background
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# @brief Play one sound synchronously.
# @details Tries multiple Windows playback backends so notification playback is
# more reliable across local host audio configurations. Falls back to a console
# beep if file playback backends fail.
if (-not (Test-Path $SoundFile)) {
    throw "Sound file not found: $SoundFile"
}

$resolvedSoundFile = (Resolve-Path $SoundFile).Path

if ($Background) {
    $child = Start-Process powershell.exe -WindowStyle Hidden -ArgumentList @(
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-File", $PSCommandPath,
        "-SoundFile", $resolvedSoundFile
    ) -PassThru
    Write-Output ("Started sound process PID {0}" -f $child.Id)
    exit 0
}

$playSucceeded = $false
$lastError = $null
$player = $null
$mediaPlayer = $null
$state = 0

try {
    Add-Type -AssemblyName System
    $player = New-Object System.Media.SoundPlayer $resolvedSoundFile
    $player.Load()
    $player.PlaySync()
    $playSucceeded = $true
} catch {
    $lastError = $_
}

if (-not $playSucceeded) {
    try {
        # WMP is used as a fallback when `SoundPlayer` cannot decode or output the file.
        $mediaPlayer = New-Object -ComObject WMPlayer.OCX
        $mediaPlayer.settings.volume = 100
        $mediaPlayer.URL = $resolvedSoundFile
        $mediaPlayer.controls.play()
        $deadline = (Get-Date).AddSeconds(15)
        do {
            Start-Sleep -Milliseconds 100
            $state = $mediaPlayer.playState
        } while ((Get-Date) -lt $deadline -and $state -ne 1)
        $playSucceeded = $true
    } catch {
        $lastError = $_
    } finally {
        if ($null -ne $mediaPlayer) {
            try { $mediaPlayer.controls.stop() } catch {}
            try { [void][System.Runtime.InteropServices.Marshal]::ReleaseComObject($mediaPlayer) } catch {}
        }
    }
}

if (-not $playSucceeded) {
    try {
        # Keep a last-resort audible cue even when file-backed playback is unavailable.
        [console]::Beep(1046, 180)
        Start-Sleep -Milliseconds 60
        [console]::Beep(1318, 240)
        $playSucceeded = $true
    } catch {
        $lastError = $_
    }
}

if (-not $playSucceeded) {
    throw "All sound playback backends failed. Last error: $lastError"
}
