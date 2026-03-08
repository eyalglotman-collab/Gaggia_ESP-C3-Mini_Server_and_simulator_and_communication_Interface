[CmdletBinding()]
param()

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$TempDir = Join-Path $ProjectRoot ".cache\detailed_design_docx_tmp"
$OutputDocx = Join-Path $ProjectRoot "docs\EyalEspressoServerSimulatorDetailedDesign.docx"

function Write-Utf8File {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,
        [Parameter(Mandatory = $true)]
        [string]$Content
    )

    $utf8 = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($Path, $Content, $utf8)
}

if (Test-Path $TempDir) {
    Remove-Item $TempDir -Recurse -Force
}

New-Item -ItemType Directory -Force $TempDir | Out-Null
New-Item -ItemType Directory -Force (Join-Path $TempDir "_rels") | Out-Null
New-Item -ItemType Directory -Force (Join-Path $TempDir "word") | Out-Null

$contentTypes = @"
<?xml version=""1.0"" encoding=""UTF-8"" standalone=""yes""?>
<Types xmlns=""http://schemas.openxmlformats.org/package/2006/content-types"">
  <Default Extension=""rels"" ContentType=""application/vnd.openxmlformats-package.relationships+xml""/>
  <Default Extension=""xml"" ContentType=""application/xml""/>
  <Override PartName=""/word/document.xml"" ContentType=""application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml""/>
</Types>
"@

$rels = @"
<?xml version=""1.0"" encoding=""UTF-8"" standalone=""yes""?>
<Relationships xmlns=""http://schemas.openxmlformats.org/package/2006/relationships"">
  <Relationship Id=""rId1"" Type=""http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument"" Target=""word/document.xml""/>
</Relationships>
"@

$documentXml = @"
<?xml version=""1.0"" encoding=""UTF-8"" standalone=""yes""?>
<w:document xmlns:w=""http://schemas.openxmlformats.org/wordprocessingml/2006/main"">
  <w:body>
    <w:p><w:r><w:t>Eyal Espresso Server Simulator Detailed Design</w:t></w:r></w:p>
    <w:p><w:r><w:t>Project Version Reference: 0.1.0</w:t></w:r></w:p>
    <w:p/>
    <w:p><w:r><w:t>1. Software Architecture</w:t></w:r></w:p>
    <w:p><w:r><w:t>Application Entry: FastAPI app entry point starts API routes and coordinates simulator services.</w:t></w:r></w:p>
    <w:p><w:r><w:t>Transport Layer: One serial manager owns the COM device and handles framing and TX or RX state.</w:t></w:r></w:p>
    <w:p><w:r><w:t>Simulation Layer: Controller state machine simulates machine state, responses, telemetry, and faults.</w:t></w:r></w:p>
    <w:p/>
    <w:p><w:r><w:t>2. Interface Design</w:t></w:r></w:p>
    <w:p><w:r><w:t>FastAPI shall expose operator and test endpoints for machine commands and telemetry.</w:t></w:r></w:p>
    <w:p/>
    <w:p><w:r><w:t>3. Configuration</w:t></w:r></w:p>
    <w:p><w:r><w:t>Versioning, protocol constants, and simulator defaults shall be kept under repository control.</w:t></w:r></w:p>
    <w:p/>
    <w:p><w:r><w:t>4. Environment and Compilation Method</w:t></w:r></w:p>
    <w:p><w:r><w:t>Recommended environment: Python, FastAPI, pyserial, uvicorn, and local tests executed from the repository root.</w:t></w:r></w:p>
    <w:sectPr/>
  </w:body>
</w:document>
"@

Write-Utf8File -Path (Join-Path $TempDir "[Content_Types].xml") -Content $contentTypes
Write-Utf8File -Path (Join-Path $TempDir "_rels\.rels") -Content $rels
Write-Utf8File -Path (Join-Path $TempDir "word\document.xml") -Content $documentXml

if (Test-Path $OutputDocx) {
    Remove-Item $OutputDocx -Force
}

Add-Type -AssemblyName System.IO.Compression.FileSystem
[System.IO.Compression.ZipFile]::CreateFromDirectory($TempDir, $OutputDocx)
Remove-Item $TempDir -Recurse -Force
