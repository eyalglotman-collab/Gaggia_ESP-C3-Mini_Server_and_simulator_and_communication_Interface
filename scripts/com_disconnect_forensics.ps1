<#
.SYNOPSIS
Runs an automated root-cause probe for simulator COM-port disconnect faults.

.DESCRIPTION
Collects synchronized runtime evidence every second and stops on the first
failure signature:
1. Backend health status (`/health`)
2. Link snapshot reachability and state (`/api/link?include_logs=0`)
3. Windows COM inventory with device names
4. Likely serial-holder processes

The script writes a JSONL trace and a concise text report under
`.cache\com_forensics`.

.PARAMETER HostName
Simulator backend host address.

.PARAMETER Port
Simulator backend port.

.PARAMETER DurationSec
Maximum probe duration in seconds.

.PARAMETER IntervalMs
Sampling interval in milliseconds.

.PARAMETER TimeoutSec
HTTP timeout (seconds) for `/health` and `/api/link`.
#>
[CmdletBinding()]
param(
    [string]$HostName = "127.0.0.1",
    [int]$Port = 8000,
    [int]$DurationSec = 180,
    [int]$IntervalMs = 1000,
    [int]$TimeoutSec = 3
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$OutputRoot = Join-Path $ProjectRoot ".cache\com_forensics"
$runStamp = Get-Date -Format "yyyyMMdd_HHmmss"
$RunDir = Join-Path $OutputRoot $runStamp
$TracePath = Join-Path $RunDir "trace.jsonl"
$ReportPath = Join-Path $RunDir "report.txt"

New-Item -ItemType Directory -Path $RunDir -Force | Out-Null

$HealthUrl = "http://$HostName`:$Port/health"
$LinkUrl = "http://$HostName`:$Port/api/link?include_logs=0"

# @brief Return an ISO8601 UTC timestamp string.
function Get-UtcIsoTimestamp {
    return (Get-Date).ToUniversalTime().ToString("o")
}

# @brief Return the list of COM ports visible to Windows.
function Get-VisibleComPorts {
    try {
        return @([System.IO.Ports.SerialPort]::GetPortNames() | Sort-Object)
    } catch {
        return @()
    }
}

# @brief Return device metadata for COM ports from PnP inventory.
function Get-ComDeviceMap {
    $entries = @()
    try {
        $raw = Get-CimInstance Win32_PnPEntity -ErrorAction Stop |
            Where-Object { $_.Name -match '\(COM[0-9]+\)' } |
            Select-Object Name, DeviceID
    } catch {
        return @()
    }

    foreach ($item in $raw) {
        $portName = $null
        if ($item.Name -match '\((COM[0-9]+)\)') {
            $portName = $matches[1]
        }

        $kind = "other"
        if ($item.Name -match "USB Serial Device") {
            $kind = "usb-serial"
        } elseif ($item.Name -match "Bluetooth") {
            $kind = "bluetooth"
        }

        $entries += [pscustomobject]@{
            port = $portName
            name = $item.Name
            device_id = $item.DeviceID
            kind = $kind
        }
    }

    return ,$entries
}

# @brief Return likely external serial-holder processes.
function Get-SerialHolderProcesses {
    $holders = @()
    $regex = 'COM[0-9]+|idf_monitor|esptool|monitor_capture|serial\.Serial|serial_for_url|pyserial|miniterm|putty|teraterm'

    try {
        $raw = Get-CimInstance Win32_Process -ErrorAction Stop |
            Where-Object { $_.CommandLine -and ($_.CommandLine -match $regex) } |
            Select-Object ProcessId, Name, CommandLine
    } catch {
        return @()
    }

    foreach ($entry in $raw) {
        $holders += [pscustomobject]@{
            pid = [int]$entry.ProcessId
            name = $entry.Name
            cmd = $entry.CommandLine
        }
    }

    return ,$holders
}

# @brief Return active simulator backend python processes.
function Get-SimulatorBackendProcesses {
    $backendProcesses = @()
    try {
        $raw = Get-CimInstance Win32_Process -ErrorAction Stop |
            Where-Object {
                $_.Name -eq "python.exe" -and
                $_.CommandLine -and
                $_.CommandLine -match "uvicorn" -and
                $_.CommandLine -match "server\.app:app" -and
                $_.CommandLine -match [Regex]::Escape($ProjectRoot)
            } |
            Select-Object ProcessId, ParentProcessId, Name, CommandLine
    } catch {
        return @()
    }

    foreach ($entry in $raw) {
        $backendProcesses += [pscustomobject]@{
            pid = [int]$entry.ProcessId
            parent_pid = [int]$entry.ParentProcessId
            name = $entry.Name
            cmd = $entry.CommandLine
        }
    }

    return ,$backendProcesses
}

# @brief Probe health endpoint with timeout.
function Get-HealthProbe {
    try {
        $resp = Invoke-WebRequest -UseBasicParsing -TimeoutSec $TimeoutSec -Uri $HealthUrl -ErrorAction Stop
        return [pscustomobject]@{
            ok = $true
            status = [int]$resp.StatusCode
            error = ""
        }
    } catch {
        return [pscustomobject]@{
            ok = $false
            status = 0
            error = $_.Exception.Message
        }
    }
}

# @brief Probe link endpoint with timeout and parse compact state.
function Get-LinkProbe {
    try {
        $resp = Invoke-WebRequest -UseBasicParsing -TimeoutSec $TimeoutSec -Uri $LinkUrl -ErrorAction Stop
        $obj = $resp.Content | ConvertFrom-Json -ErrorAction Stop
        return [pscustomobject]@{
            ok = $true
            status = [int]$resp.StatusCode
            error = ""
            current_state = [string]$obj.current_state
            serial_port = [string]$obj.config.serial_port
            runtime_last_error = [string]$obj.last_error
            transport_port_open = [bool]$obj.transport.port_open
            transport_last_error = [string]$obj.transport.last_error
            transport_last_event = [string]$obj.transport.last_event
            usb_total_kbytes_per_sec = [double]$obj.usb_total_kbytes_per_sec
        }
    } catch {
        return [pscustomobject]@{
            ok = $false
            status = 0
            error = $_.Exception.Message
            current_state = ""
            serial_port = ""
            runtime_last_error = ""
            transport_port_open = $false
            transport_last_error = ""
            transport_last_event = ""
            usb_total_kbytes_per_sec = 0.0
        }
    }
}

# @brief Build one synchronized sample for trace logging.
function Get-ForensicsSample {
    param(
        [string]$KnownPort
    )

    $health = Get-HealthProbe
    $link = Get-LinkProbe
    $deviceMap = Get-ComDeviceMap
    $ports = Get-VisibleComPorts
    $holders = Get-SerialHolderProcesses
    $simBackends = Get-SimulatorBackendProcesses

    $simPidMap = @{}
    foreach ($proc in $simBackends) {
        $simPidMap[[int]$proc.pid] = $true
    }
    $simBackendRoots = @($simBackends | Where-Object { -not $simPidMap.ContainsKey([int]$_.parent_pid) })

    $targetPort = $KnownPort
    if ([string]::IsNullOrWhiteSpace($targetPort) -and -not [string]::IsNullOrWhiteSpace($link.serial_port)) {
        $targetPort = $link.serial_port
    }

    $targetPresent = $false
    if (-not [string]::IsNullOrWhiteSpace($targetPort)) {
        $targetPresent = $ports -contains $targetPort
    }

    $targetDevice = $null
    if (-not [string]::IsNullOrWhiteSpace($targetPort)) {
        $targetDevice = $deviceMap | Where-Object { $_.port -eq $targetPort } | Select-Object -First 1
    }

    $targetHolders = @()
    if (-not [string]::IsNullOrWhiteSpace($targetPort)) {
        $targetHolders = $holders | Where-Object { $_.cmd -match [Regex]::Escape($targetPort) }
    }

    return [pscustomobject]@{
        ts_utc = Get-UtcIsoTimestamp
        health = $health
        link = $link
        target_port = $targetPort
        target_port_present = $targetPresent
        target_port_device = $targetDevice
        visible_ports = $ports
        device_map = $deviceMap
        target_holders = $targetHolders
        serial_holders = $holders
        simulator_backends = $simBackends
        simulator_backend_root_count = $simBackendRoots.Count
    }
}

# @brief Evaluate sample for first failure signature.
function Get-FailureSignature {
    param(
        [pscustomobject]$Sample,
        [pscustomobject]$Previous
    )

    if (-not $Sample.health.ok -or $Sample.health.status -ne 200) {
        return [pscustomobject]@{
            has_failure = $true
            code = "backend-unhealthy"
            reason = "Simulator backend health endpoint is not healthy."
        }
    }

    if (-not $Sample.link.ok) {
        return [pscustomobject]@{
            has_failure = $true
            code = "link-endpoint-blocked"
            reason = "Link snapshot endpoint timed out or failed while backend remained healthy."
        }
    }

    if ($Sample.link.current_state -eq "error" -and -not [string]::IsNullOrWhiteSpace($Sample.link.runtime_last_error)) {
        return [pscustomobject]@{
            has_failure = $true
            code = "runtime-error-state"
            reason = "Link runtime entered error state."
        }
    }

    if ($Previous -and $Previous.link.ok -and $Previous.link.transport_port_open -and -not $Sample.link.transport_port_open -and $Sample.link.current_state -ne "reset") {
        return [pscustomobject]@{
            has_failure = $true
            code = "port-open-dropped"
            reason = "Transport port transitioned from open to closed during active runtime."
        }
    }

    return [pscustomobject]@{
        has_failure = $false
        code = ""
        reason = ""
    }
}

# @brief Build root-cause verdict from first failure sample.
function Get-RootCauseVerdict {
    param(
        [pscustomobject]$Sample,
        [pscustomobject]$Signature
    )

    $evidence = New-Object System.Collections.Generic.List[string]
    $targetPort = $Sample.target_port
    if (-not [string]::IsNullOrWhiteSpace($targetPort)) {
        $evidence.Add("Target port: $targetPort")
    }

    if ($Sample.simulator_backends.Count -gt 1) {
        $evidence.Add("Simulator backend process count: $($Sample.simulator_backends.Count)")
        $evidence.Add("Simulator backend root count: $($Sample.simulator_backend_root_count)")
    }

    $openTransportCallers = @($Sample.serial_holders | Where-Object {
        $_.name -eq "powershell.exe" -and $_.cmd -match "/api/transport/open"
    })
    if ($Signature.code -eq "link-endpoint-blocked" -and $openTransportCallers.Count -gt 0) {
        $callerPids = ($openTransportCallers | ForEach-Object { "$($_.pid):$($_.name)" }) -join ", "
        $evidence.Add("In-flight open_transport callers detected: $callerPids")
        return [pscustomobject]@{
            verdict = "Stalled transport-open request blocked link runtime"
            reason = "An open-transport caller remained active while link snapshots timed out."
            evidence = $evidence
        }
    }

    if (-not [string]::IsNullOrWhiteSpace($targetPort) -and -not $Sample.target_port_present) {
        $evidence.Add("Target port is missing from visible COM inventory.")
        return [pscustomobject]@{
            verdict = "USB re-enumeration or physical link instability"
            reason = "Configured COM port disappeared from Windows during/after failure."
            evidence = $evidence
        }
    }

    if ($Sample.target_holders.Count -gt 0) {
        $pids = ($Sample.target_holders | ForEach-Object { "$($_.pid):$($_.name)" }) -join ", "
        $evidence.Add("Processes referencing target port: $pids")
        return [pscustomobject]@{
            verdict = "COM ownership conflict"
            reason = "Another process appears to be attached to the configured COM port."
            evidence = $evidence
        }
    }

    if ($Sample.simulator_backend_root_count -gt 1) {
        return [pscustomobject]@{
            verdict = "Duplicate simulator backends causing serial contention"
            reason = "More than one simulator backend root process is running simultaneously."
            evidence = $evidence
        }
    }

    $transportError = $Sample.link.transport_last_error
    if ($transportError -match "Access is denied|PermissionError") {
        $evidence.Add("Transport error: $transportError")
        return [pscustomobject]@{
            verdict = "COM ownership conflict"
            reason = "Serial layer reported access denial while port remained visible."
            evidence = $evidence
        }
    }

    if ($transportError -match "read failed|device|I/O|timeout") {
        $evidence.Add("Transport error: $transportError")
        return [pscustomobject]@{
            verdict = "Serial driver or line instability"
            reason = "Serial read path failed while backend remained alive."
            evidence = $evidence
        }
    }

    if ($Signature.code -eq "link-endpoint-blocked" -and $Sample.health.ok -and $Sample.health.status -eq 200) {
        return [pscustomobject]@{
            verdict = "Link runtime blocked or lock contention"
            reason = "Health endpoint stayed responsive while link snapshot endpoint failed."
            evidence = $evidence
        }
    }

    return [pscustomobject]@{
        verdict = "Unknown (captured forensics required)"
        reason = "Failure detected but no single dominant signature matched."
        evidence = $evidence
    }
}

# @brief Persist sample to JSONL trace.
function Write-SampleTraceLine {
    param(
        [pscustomobject]$Sample
    )

    $json = $Sample | ConvertTo-Json -Depth 8 -Compress
    Add-Content -Path $TracePath -Value $json
}

Write-Host "COM disconnect forensics started."
Write-Host "Health URL: $HealthUrl"
Write-Host "Link URL  : $LinkUrl"
Write-Host "Run dir   : $RunDir"

$deadline = (Get-Date).AddSeconds($DurationSec)
$knownPort = ""
$sampleIndex = 0
$previousSample = $null
$failureSignature = $null
$failureSample = $null

while ((Get-Date) -lt $deadline) {
    $sample = Get-ForensicsSample -KnownPort $knownPort
    $sampleIndex += 1

    if (-not [string]::IsNullOrWhiteSpace($sample.target_port)) {
        $knownPort = $sample.target_port
    }

    Write-SampleTraceLine -Sample $sample

    $sig = Get-FailureSignature -Sample $sample -Previous $previousSample
    $stateText = $sample.link.current_state
    if ([string]::IsNullOrWhiteSpace($stateText)) {
        $stateText = "n/a"
    }

    Write-Host ("[{0}] health={1} link={2} state={3} port={4} open={5}" -f
        $sampleIndex,
        $(if ($sample.health.ok) { $sample.health.status } else { "fail" }),
        $(if ($sample.link.ok) { "ok" } else { "fail" }),
        $stateText,
        $(if ([string]::IsNullOrWhiteSpace($sample.target_port)) { "n/a" } else { $sample.target_port }),
        $sample.link.transport_port_open
    )

    if ($sig.has_failure) {
        $failureSignature = $sig
        $failureSample = $sample
        break
    }

    $previousSample = $sample
    Start-Sleep -Milliseconds $IntervalMs
}

$reportLines = New-Object System.Collections.Generic.List[string]
$reportLines.Add("COM Disconnect Forensics Report")
$reportLines.Add("Run Timestamp (local): $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')")
$reportLines.Add("Run Directory: $RunDir")
$reportLines.Add("Trace File: $TracePath")
$reportLines.Add("")

if ($failureSample -ne $null) {
    $verdict = Get-RootCauseVerdict -Sample $failureSample -Signature $failureSignature
    $reportLines.Add("Failure Detected: YES")
    $reportLines.Add("Failure Code: $($failureSignature.code)")
    $reportLines.Add("Failure Reason: $($failureSignature.reason)")
    $reportLines.Add("Verdict: $($verdict.verdict)")
    $reportLines.Add("Why: $($verdict.reason)")
    $reportLines.Add("")
    $reportLines.Add("Primary Evidence:")
    $reportLines.Add("- Timestamp UTC: $($failureSample.ts_utc)")
    $reportLines.Add("- Health: ok=$($failureSample.health.ok) status=$($failureSample.health.status)")
    $reportLines.Add("- Link: ok=$($failureSample.link.ok) state=$($failureSample.link.current_state)")
    $reportLines.Add("- Runtime last_error: $($failureSample.link.runtime_last_error)")
    $reportLines.Add("- Transport last_error: $($failureSample.link.transport_last_error)")
    $reportLines.Add("- Target port: $($failureSample.target_port)")
    $reportLines.Add("- Target present: $($failureSample.target_port_present)")
    $reportLines.Add("- Visible ports: $($failureSample.visible_ports -join ', ')")
    $reportLines.Add("- Simulator backend count: $($failureSample.simulator_backends.Count)")
    $reportLines.Add("- Simulator backend root count: $($failureSample.simulator_backend_root_count)")
    $reportLines.Add("- Target holders: $($failureSample.target_holders.Count)")
    foreach ($line in $verdict.evidence) {
        $reportLines.Add("- $line")
    }
} else {
    $reportLines.Add("Failure Detected: NO")
    $reportLines.Add("Result: No COM failure signature detected during the sampling window.")
}

$reportLines | Set-Content -Path $ReportPath -Encoding UTF8

Write-Host ""
Write-Host "Forensics complete."
if ($failureSample -ne $null) {
    Write-Host "Failure code: $($failureSignature.code)"
    $verdict = Get-RootCauseVerdict -Sample $failureSample -Signature $failureSignature
    Write-Host "Verdict     : $($verdict.verdict)"
}
Write-Host "Report file : $ReportPath"
Write-Host "Trace file  : $TracePath"
