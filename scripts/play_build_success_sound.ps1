[CmdletBinding()]
param()

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$PlaybackScript = Join-Path $ProjectRoot "scripts\play_wait_sound.ps1"
$SoundFile = Join-Path $ProjectRoot "sounds\build-success-monkey-1p5x.wav"

# @brief Play the project build-success sound once.
# @details Reuses the common playback helper so build notifications use the same
# backend selection and fallback behavior as wait notifications.
if (-not (Test-Path $PlaybackScript)) {
    throw "Playback helper not found: $PlaybackScript"
}

if (-not (Test-Path $SoundFile)) {
    throw "Build-success sound file not found: $SoundFile"
}

powershell.exe -NoProfile -ExecutionPolicy Bypass -File $PlaybackScript -SoundFile $SoundFile
