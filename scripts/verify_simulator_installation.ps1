<#
.SYNOPSIS
Verifies that the local simulator runtime matches the development baseline.

.DESCRIPTION
Checks the repository-local Python interpreter and required runtime package
versions before the manual batch launcher tries to start any backend or UI
logic. If a required component is missing or the version differs from the
development baseline, the script shows a popup listing the gaps and exits with a
non-zero status.
#>
[CmdletBinding()]
param()

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$PythonExe = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
$ExpectedPythonVersion = '3.13.3'
$ExpectedPackageVersions = [ordered]@{
    fastapi = '0.135.1'
    uvicorn = '0.41.0'
    pyserial = '3.5'
}

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
        'The following gaps or version mismatches were detected:'
        ''
    ) + $Gaps + @(
        ''
        'Install or align these components, then run the launcher again.'
    )

    [void][System.Windows.Forms.MessageBox]::Show(
        ($message -join "`r`n"),
        'Simulator Installation Verification',
        [System.Windows.Forms.MessageBoxButtons]::OK,
        [System.Windows.Forms.MessageBoxIcon]::Warning
    )
}

$gaps = New-Object System.Collections.Generic.List[string]

if (-not (Test-Path $PythonExe)) {
    $gaps.Add("Missing repository-local Python interpreter: $PythonExe")
} else {
    $pythonVersionOutput = & $PythonExe --version 2>&1
    if ($LASTEXITCODE -ne 0) {
        $gaps.Add("Failed to query Python version from $PythonExe")
    } else {
        $actualPythonVersion = ($pythonVersionOutput -replace '^Python\s+', '').Trim()
        if ($actualPythonVersion -ne $ExpectedPythonVersion) {
            $gaps.Add("Python version mismatch. Expected $ExpectedPythonVersion, found $actualPythonVersion.")
        }
    }

    $packageCheckScript = @'
from importlib import metadata
packages = ["fastapi", "uvicorn", "pyserial"]
for name in packages:
    try:
        print("{}={}".format(name, metadata.version(name)))
    except metadata.PackageNotFoundError:
        print("{}=MISSING".format(name))
'@

    # Feed the probe script over stdin because PowerShell can mangle quotes in
    # inline `python -c` payloads on some Windows hosts.
    $packageLines = @($packageCheckScript | & $PythonExe - 2>$null)
    $packageVersions = @{}
    foreach ($packageLine in $packageLines) {
        if ($packageLine -match '^(?<name>[^=]+)=(?<version>.+)$') {
            $packageVersions[$matches.name] = $matches.version
        }
    }

    foreach ($packageName in $ExpectedPackageVersions.Keys) {
        $expectedVersion = $ExpectedPackageVersions[$packageName]
        $actualVersion = $packageVersions[$packageName]
        if (-not $actualVersion) {
            $gaps.Add("Package check did not return a version for $packageName.")
            continue
        }

        if ($actualVersion -eq 'MISSING') {
            $gaps.Add("Missing Python package: $packageName==$expectedVersion")
            continue
        }

        if ($actualVersion -ne $expectedVersion) {
            $gaps.Add("Package version mismatch for $packageName. Expected $expectedVersion, found $actualVersion.")
        }
    }
}

if ($gaps.Count -gt 0) {
    Show-GapMessage -Gaps $gaps.ToArray()
    exit 1
}

exit 0
