<#
.SYNOPSIS
Captures a bounded slice of serial output from the simulator bridge port.

.DESCRIPTION
Opens the requested serial port without resetting the target, reads available
data for a fixed duration, and writes the raw text chunks to stdout. This
serves as the fallback monitor path on hosts where the normal ESP-IDF monitor
cannot attach cleanly.

.PARAMETER Port
Serial port name to open.

.PARAMETER BaudRate
Serial baud rate used for capture.

.PARAMETER DurationSec
Maximum capture duration in seconds.
#>
[CmdletBinding()]
param(
    [string]$Port = "COM4",
    [int]$BaudRate = 115200,
    [int]$DurationSec = 20
)

$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System

$serial = New-Object System.IO.Ports.SerialPort $Port, $BaudRate, ([System.IO.Ports.Parity]::None), 8, ([System.IO.Ports.StopBits]::One)
$serial.ReadTimeout = 250
$serial.NewLine = "`n"

try {
    $serial.Open()
    $deadline = (Get-Date).AddSeconds($DurationSec)
    while ((Get-Date) -lt $deadline) {
        try {
            # `ReadExisting` avoids blocking the loop while still draining bursts quickly.
            $chunk = $serial.ReadExisting()
            if ($chunk) {
                Write-Output $chunk
            } else {
                Start-Sleep -Milliseconds 100
            }
        } catch {
            Start-Sleep -Milliseconds 100
        }
    }
} finally {
    if ($serial.IsOpen) {
        $serial.Close()
    }
    $serial.Dispose()
}
