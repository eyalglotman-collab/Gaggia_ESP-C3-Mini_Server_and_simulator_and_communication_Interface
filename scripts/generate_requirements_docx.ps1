[CmdletBinding()]
param()

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$TempDir = Join-Path $ProjectRoot ".cache\requirements_docx_tmp"
$OutputDocx = Join-Path $ProjectRoot "docs\EyalEspressoServerSimulatorRequirements and Design.docx"

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
    <w:p><w:r><w:t>Eyal Espresso Server Simulator Requirements and Design</w:t></w:r></w:p>
    <w:p><w:r><w:t>Document Status: Working Baseline</w:t></w:r></w:p>
    <w:p><w:r><w:t>Version: 0.1.0</w:t></w:r></w:p>
    <w:p><w:r><w:t>Reviewed on: 08-Mar-26 15:45:00</w:t></w:r></w:p>
    <w:p/>
    <w:p><w:r><w:t>1. Product Overview</w:t></w:r></w:p>
    <w:p><w:r><w:t>Purpose: Simulate the Gaggia controller side of the espresso system and expose test and operator controls through FastAPI.</w:t></w:r></w:p>
    <w:p/>
    <w:p><w:r><w:t>2. Functional Requirements</w:t></w:r></w:p>
    <w:p><w:r><w:t>FR-001: The simulator shall own one serial COM interface through a single background transport manager.</w:t></w:r></w:p>
    <w:p><w:r><w:t>FR-002: The simulator shall expose get and set operations through FastAPI endpoints.</w:t></w:r></w:p>
    <w:p><w:r><w:t>FR-003: The simulator shall provide controller telemetry and command logging.</w:t></w:r></w:p>
    <w:p/>
    <w:p><w:r><w:t>3. Architecture Notes</w:t></w:r></w:p>
    <w:p><w:r><w:t>Modules: API layer, serial transport layer, simulator state machine, and tests.</w:t></w:r></w:p>
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
