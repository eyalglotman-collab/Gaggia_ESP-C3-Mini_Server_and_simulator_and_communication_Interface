<#
.SYNOPSIS
Stops the detached simulator wait-sound worker.

.DESCRIPTION
Reads the PID file created by `start_wait_sound.ps1`, terminates that worker if
it is still alive, and removes the recorded handle so later wait cycles start
from a clean state.
#>
[CmdletBinding()]
param()

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$PidFile = Join-Path $ProjectRoot ".cache\wait_sound.pid"

# @brief Stop the detached wait-sound worker if it exists.
# @details Reads the PID handle created by `start_wait_sound.ps1`, terminates
# that process if still alive, and removes the stale handle afterwards.
function Stop-WaitWorker {
    if (-not (Test-Path $PidFile)) {
        Write-Output "No wait sound worker is running."
        return
    }

    $workerPid = (Get-Content $PidFile -ErrorAction SilentlyContinue | Select-Object -First 1)
    if ($workerPid) {
        $worker = Get-Process -Id $workerPid -ErrorAction SilentlyContinue
        if ($worker) {
            Stop-Process -Id $workerPid -Force
            Write-Output "Stopped wait sound worker PID $workerPid."
        } else {
            Write-Output "Removed stale wait sound worker handle for PID $workerPid."
        }
    }

    Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
}

Stop-WaitWorker
