<#
.SYNOPSIS
Verifies that the local simulator runtime matches the development baseline.

.DESCRIPTION
Runs requirements synchronization with the repository-local Python environment
before startup. On failure, the script reports actionable gaps and pip output.
#>
[CmdletBinding()]
param()

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$PythonExe = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
$RequirementsFile = Join-Path $ProjectRoot 'requirements.txt'

# @brief Show a blocking Windows message box with the detected installation gaps.
# @details Uses Windows Forms so the launcher can explain local environment
# mismatches before any backend process is started.
# @param[in] Gaps Ordered list of human-readable gap descriptions.
function Show-GapMessage {
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$Gaps
    )

    Add-Type -AssemblyName System.Windows.Forms
    $message = @(
        'Simulator installation verification failed.'
        ''
        'The following gaps were detected:'
        ''
    ) + $Gaps + @(
        ''
        'Fix these gaps and run the launcher again.'
    )

    [void][System.Windows.Forms.MessageBox]::Show(
        ($message -join "`r`n"),
        'Simulator Installation Verification',
        [System.Windows.Forms.MessageBoxButtons]::OK,
        [System.Windows.Forms.MessageBoxIcon]::Warning
    )
}

# @brief Synchronize Python requirements and parse missing-package failures.
# @details Runs `pip install -r requirements.txt` with the repository-local
# interpreter, captures pip output, and extracts package names from common
# "could not find distribution" error lines.
# @return Hashtable with Success, MissingPackages, Output, PipExitCode, and
# RequirementsFile.
function Invoke-SimulatorRequirementsSync {
    $result = @{
        Success = $false
        MissingPackages = @()
        Output = @()
        PipExitCode = $null
        RequirementsFile = $RequirementsFile
    }

    if (-not (Test-Path $PythonExe)) {
        $result.Output = @("Missing repository-local Python interpreter: $PythonExe")
        return $result
    }

    if (-not (Test-Path $RequirementsFile)) {
        $result.Output = @("Missing requirements file: $RequirementsFile")
        return $result
    }

    $pipOutput = @(& $PythonExe -m pip install -r $RequirementsFile --disable-pip-version-check 2>&1)
    $pipSucceeded = $?
    $pipExitCode = $LASTEXITCODE
    if ($null -eq $pipExitCode) {
        $pipExitCode = 0
    }

    $missingPackages = New-Object System.Collections.Generic.List[string]
    foreach ($line in $pipOutput) {
        if ($line -match 'Could not find a version that satisfies the requirement\s+(?<name>[^\s;]+)') {
            $missingPackages.Add($matches.name)
            continue
        }

        if ($line -match 'No matching distribution found for\s+(?<name>[^\s;]+)') {
            $missingPackages.Add($matches.name)
            continue
        }
    }

    $result.Success = ($pipSucceeded -and ($pipExitCode -eq 0))
    $result.MissingPackages = @($missingPackages | Select-Object -Unique)
    $result.Output = $pipOutput
    $result.PipExitCode = $pipExitCode
    return $result
}

# @brief Return static installation gaps that do not depend on Python stdout parsing.
# @details Checks only stable prerequisites so verification remains reliable in
# shell-host combinations where native child stdout may be unavailable.
# @return List of human-readable gap descriptions.
function Get-SimulatorInstallationGaps {
    $gaps = New-Object System.Collections.Generic.List[string]

    if (-not (Test-Path $PythonExe)) {
        $gaps.Add("Missing repository-local Python interpreter: $PythonExe")
    }

    if (-not (Test-Path $RequirementsFile)) {
        $gaps.Add("Missing requirements file: $RequirementsFile")
    }

    return $gaps.ToArray()
}

if ($MyInvocation.InvocationName -ne '.') {
    $requirementsSync = Invoke-SimulatorRequirementsSync
    if (-not $requirementsSync.Success) {
        $gaps = New-Object System.Collections.Generic.List[string]
        $gaps.Add("Automatic requirements sync failed: $($requirementsSync.RequirementsFile)")
        $gaps.Add("pip exit code: $($requirementsSync.PipExitCode)")

        if ($requirementsSync.MissingPackages.Count -gt 0) {
            foreach ($packageName in $requirementsSync.MissingPackages) {
                $gaps.Add("Missing package from pip resolution: $packageName")
            }
        }

        $pipTail = @($requirementsSync.Output | Select-Object -Last 8)
        if ($pipTail.Count -gt 0) {
            $gaps.Add('pip output (tail):')
            foreach ($line in $pipTail) {
                $gaps.Add("  $line")
            }
        }

        Show-GapMessage -Gaps $gaps.ToArray()
        exit 1
    }

    $gaps = Get-SimulatorInstallationGaps
    if ($gaps.Count -gt 0) {
        Show-GapMessage -Gaps $gaps
        exit 1
    }

    exit 0
}
