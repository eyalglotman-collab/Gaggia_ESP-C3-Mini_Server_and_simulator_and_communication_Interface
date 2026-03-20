<#
.SYNOPSIS
Captures a bounded slice of serial output from the simulator bridge port.

.DESCRIPTION
Opens the requested serial port without resetting the target, reads available
bytes for a fixed duration, and prints a textual stream:
- plain ESP log lines stay textual
- framed binary transport packets are decoded to human-readable summaries

.PARAMETER Port
Serial port name to open.

.PARAMETER BaudRate
Serial baud rate used for capture.

.PARAMETER DurationSec
Maximum capture duration in seconds.

.PARAMETER Raw
When set, also emits undecoded printable chunks from non-frame bytes.
#>
[CmdletBinding()]
param(
    [string]$Port = "COM4",
    [int]$BaudRate = 115200,
    [int]$DurationSec = 20,
    [switch]$Raw
)

$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System

# @brief Release the COM port held by the simulator before opening it for capture.
# @details POSTs to the simulator HTTP API to force-release the serial link.
# All errors are suppressed so monitor proceeds even when the sim is not running.
function Invoke-SimulatorComRelease {
    try {
        Invoke-WebRequest -Uri 'http://localhost:8000/api/transport/release-com' `
            -Method Post -TimeoutSec 3 -UseBasicParsing -ErrorAction SilentlyContinue | Out-Null
    } catch { }
    Start-Sleep -Milliseconds 500
}

Write-Host "Releasing COM port before monitor..."
Invoke-SimulatorComRelease

# @brief Compute CRC16-CCITT for a byte array.
# @details Mirrors the transport CRC used by the bridge framed protocol.
# @param[in] Data Bytes to checksum.
# @return 16-bit CRC integer.
function Get-Crc16Ccitt {
    param(
        [Parameter(Mandatory = $true)]
        [byte[]]$Data
    )

    $crc = 0xFFFF
    foreach ($byte in $Data) {
        $crc = $crc -bxor ($byte -shl 8)
        for ($i = 0; $i -lt 8; $i++) {
            if (($crc -band 0x8000) -ne 0) {
                $crc = (($crc -shl 1) -bxor 0x1021) -band 0xFFFF
            } else {
                $crc = ($crc -shl 1) -band 0xFFFF
            }
        }
    }

    return $crc
}

# @brief Translate protocol message type IDs into stable text labels.
# @param[in] TypeId One-byte message type value from a frame header.
# @return Text label for known type IDs.
function Get-MessageTypeName {
    param(
        [Parameter(Mandatory = $true)]
        [int]$TypeId
    )

    switch ($TypeId) {
        1 { return 'RESET' }
        2 { return 'INITIALIZE' }
        3 { return 'CONNECT' }
        4 { return 'DISCONNECT' }
        5 { return 'KEEPALIVE' }
        6 { return 'ERROR' }
        7 { return 'ACK' }
        8 { return 'DATA' }
        default { return "TYPE_$TypeId" }
    }
}

# @brief Emit printable ASCII text from non-frame bytes.
# @details Keeps line assembly stable across chunk boundaries and suppresses
# non-printable noise from mixed binary traffic.
# @param[in] Bytes Raw byte segment to decode as text.
# @param[in,out] TextBuilder Shared mutable line buffer.
# @param[in] Raw Emit text even when it does not match known log prefixes.
function Emit-PrintableTextFromBytes {
    param(
        [Parameter(Mandatory = $true)]
        [byte[]]$Bytes,
        [Parameter(Mandatory = $true)]
        [System.Text.StringBuilder]$TextBuilder,
        [Parameter(Mandatory = $true)]
        [bool]$Raw
    )

    foreach ($b in $Bytes) {
        if ($b -eq 10) {
            if ($TextBuilder.Length -gt 0) {
                $line = $TextBuilder.ToString().Trim()
                $TextBuilder.Clear() | Out-Null
                if ($line.Length -gt 0) {
                    if ($Raw -or $line -match '^[IWE]\s*\(' -or $line -match 'bridge:') {
                        Write-Output $line
                    }
                }
            }
            continue
        }

        if ($b -eq 13) {
            continue
        }

        if (($b -ge 32 -and $b -le 126) -or $b -eq 9) {
            [void]$TextBuilder.Append([char]$b)
        }
    }
}

$serial = New-Object System.IO.Ports.SerialPort $Port, $BaudRate, ([System.IO.Ports.Parity]::None), 8, ([System.IO.Ports.StopBits]::One)
$serial.ReadTimeout = 250
$serial.WriteTimeout = 250

$frameBuffer = New-Object System.Collections.Generic.List[byte]
$textBuilder = New-Object System.Text.StringBuilder
$readBuffer = New-Object byte[] 512

try {
    $serial.Open()
    $deadline = (Get-Date).AddSeconds($DurationSec)

    while ((Get-Date) -lt $deadline) {
        $available = $serial.BytesToRead
        if ($available -le 0) {
            Start-Sleep -Milliseconds 100
            continue
        }

        $readCount = $serial.Read($readBuffer, 0, [Math]::Min($available, $readBuffer.Length))
        if ($readCount -le 0) {
            Start-Sleep -Milliseconds 50
            continue
        }

        for ($i = 0; $i -lt $readCount; $i++) {
            $frameBuffer.Add($readBuffer[$i])
        }

        while ($true) {
            $sofIndex = -1
            for ($i = 0; $i -lt ($frameBuffer.Count - 1); $i++) {
                if ($frameBuffer[$i] -eq 0xA5 -and $frameBuffer[$i + 1] -eq 0x5A) {
                    $sofIndex = $i
                    break
                }
            }

            if ($sofIndex -lt 0) {
                if ($frameBuffer.Count -gt 0) {
                    Emit-PrintableTextFromBytes -Bytes $frameBuffer.ToArray() -TextBuilder $textBuilder -Raw:$Raw.IsPresent
                    $frameBuffer.Clear()
                }
                break
            }

            if ($sofIndex -gt 0) {
                $prefixBytes = New-Object byte[] $sofIndex
                for ($j = 0; $j -lt $sofIndex; $j++) {
                    $prefixBytes[$j] = $frameBuffer[$j]
                }
                Emit-PrintableTextFromBytes -Bytes $prefixBytes -TextBuilder $textBuilder -Raw:$Raw.IsPresent
                $frameBuffer.RemoveRange(0, $sofIndex)
            }

            $headerBytes = 15
            $crcBytes = 2
            if ($frameBuffer.Count -lt ($headerBytes + $crcBytes)) {
                break
            }

            $payloadLength = [int]$frameBuffer[3] + ([int]$frameBuffer[4] -shl 8)
            $totalLength = $headerBytes + $payloadLength + $crcBytes
            if ($frameBuffer.Count -lt $totalLength) {
                break
            }

            $packet = New-Object byte[] $totalLength
            for ($j = 0; $j -lt $totalLength; $j++) {
                $packet[$j] = $frameBuffer[$j]
            }

            $expectedCrc = [int]$packet[$totalLength - 2] + ([int]$packet[$totalLength - 1] -shl 8)
            $packetWithoutCrc = New-Object byte[] ($totalLength - 2)
            [Array]::Copy($packet, 0, $packetWithoutCrc, 0, $totalLength - 2)
            $actualCrc = Get-Crc16Ccitt -Data $packetWithoutCrc

            if ($actualCrc -ne $expectedCrc) {
                $frameBuffer.RemoveAt(0)
                continue
            }

            $typeId = [int]$packet[2]
            $typeName = Get-MessageTypeName -TypeId $typeId
            $hostLive = [BitConverter]::ToUInt32($packet, 5)
            $deviceLive = [BitConverter]::ToUInt32($packet, 9)
            $sequence = [BitConverter]::ToUInt16($packet, 13)

            $payloadText = '<empty>'
            if ($payloadLength -gt 0) {
                $payloadBytes = New-Object byte[] $payloadLength
                [Array]::Copy($packet, 15, $payloadBytes, 0, $payloadLength)
                $decoded = [System.Text.Encoding]::UTF8.GetString($payloadBytes)
                $decoded = ($decoded -replace '[^\x20-\x7E]', '').Trim()
                if ($decoded.Length -gt 0) {
                    $payloadText = $decoded
                }
            }

            Write-Output ("FRAME {0,-10} seq={1,5} host={2,10} device={3,10} payload={4}" -f $typeName, $sequence, $hostLive, $deviceLive, $payloadText)
            $frameBuffer.RemoveRange(0, $totalLength)
        }
    }

    if ($frameBuffer.Count -gt 0) {
        Emit-PrintableTextFromBytes -Bytes $frameBuffer.ToArray() -TextBuilder $textBuilder -Raw:$Raw.IsPresent
        $frameBuffer.Clear()
    }

    if ($textBuilder.Length -gt 0 -and $Raw.IsPresent) {
        $tail = $textBuilder.ToString().Trim()
        if ($tail.Length -gt 0) {
            Write-Output $tail
        }
    }
} finally {
    if ($serial.IsOpen) {
        $serial.Close()
    }
    $serial.Dispose()
}
