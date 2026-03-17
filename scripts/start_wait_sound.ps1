<#
.SYNOPSIS
Starts the simulator wait-sound notification flow.

.DESCRIPTION
Plays the configured wait sound once immediately, then launches or reuses a
hidden worker that repeats the sound at a fixed interval while VS Code remains
open. The worker PID is stored in `.cache\wait_sound.pid` so it can be stopped
as soon as user interaction resumes.

.PARAMETER Worker
Internal switch used when the script is relaunched as the detached repeat
worker.

.PARAMETER IntervalSeconds
Delay between repeat notifications while the worker is active.
#>
[CmdletBinding()]
param(
    [switch]$Worker,
    [int]$IntervalSeconds = 180
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$PidFile = Join-Path $ProjectRoot ".cache\wait_sound.pid"
$SoundFile = Join-Path $ProjectRoot "sounds\WaitSound.wav"
$PlaybackScript = Join-Path $ProjectRoot "scripts\play_wait_sound.ps1"

# @brief Check whether VS Code is still running on the host.
# @details The wait-sound worker exits automatically if no `Code` process is
# found, which prevents orphaned sound loops after the editor is closed.
function Test-VsCodeRunning {
    return [bool](Get-Process -Name Code -ErrorAction SilentlyContinue)
}

# @brief Play the configured wait sound once.
# @details Runs a dedicated playback helper process so the hidden wait worker
# remains alive even if a single playback attempt fails.
function Play-WaitSound {
    $playProc = Start-Process powershell.exe -ArgumentList @(
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-File", $PlaybackScript,
        "-SoundFile", $SoundFile
    ) -PassThru -Wait

    if ($playProc.ExitCode -ne 0) {
        throw "Wait sound playback helper failed with exit code $($playProc.ExitCode)."
    }
}

# @brief Run the repeating wait-sound loop.
# @details Sleeps for the configured interval, re-checks the PID handle, and
# only continues while the handle still points at the current worker process.
# @param[in] IntervalSeconds Delay between wait-sound replays.
function Start-WaitLoop {
    param(
        [Parameter(Mandatory = $true)]
        [int]$IntervalSeconds
    )

    while ($true) {
        Start-Sleep -Seconds $IntervalSeconds
        if (-not (Test-VsCodeRunning)) {
            Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
            break
        }

        if (-not (Test-Path $PidFile)) {
            break
        }

        $trackedPid = (Get-Content $PidFile -ErrorAction SilentlyContinue | Select-Object -First 1)
        if ($trackedPid -ne "$PID") {
            break
        }

        Play-WaitSound
    }
}

# @brief Start the detached wait-sound worker if one is not already running.
# @details Persists the worker PID in `.cache/wait_sound.pid` so a separate
# stop helper can terminate it as soon as user interaction resumes.
function Start-WaitWorker {
    if (-not (Test-VsCodeRunning)) {
        throw "VS Code is not running, so the wait sound worker will not be started."
    }

    if (Test-Path $PidFile) {
        $existingPid = (Get-Content $PidFile -ErrorAction SilentlyContinue | Select-Object -First 1)
        if ($existingPid) {
            $existing = Get-Process -Id $existingPid -ErrorAction SilentlyContinue
            if ($existing) {
                Write-Output "Wait sound worker already running with PID $existingPid."
                return
            }
        }
        Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
    }

    New-Item -ItemType Directory -Force (Split-Path -Parent $PidFile) | Out-Null
    # Relaunch the same script as a hidden worker so the chat-side caller can return immediately.
    $command = "& '$PSCommandPath' -Worker -IntervalSeconds $IntervalSeconds"
    $proc = Start-Process powershell.exe -ArgumentList @("-NoProfile", "-WindowStyle", "Hidden", "-ExecutionPolicy", "Bypass", "-Command", $command) -PassThru
    Set-Content -Path $PidFile -Value $proc.Id -NoNewline
    Write-Output "Started wait sound worker PID $($proc.Id)."
}

if (-not (Test-Path $SoundFile)) {
    throw "Wait sound file not found: $SoundFile"
}
if (-not (Test-Path $PlaybackScript)) {
    throw "Wait playback helper not found: $PlaybackScript"
}

if ($Worker) {
    Start-WaitLoop -IntervalSeconds $IntervalSeconds
    exit 0
}

Play-WaitSound
Start-WaitWorker
