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