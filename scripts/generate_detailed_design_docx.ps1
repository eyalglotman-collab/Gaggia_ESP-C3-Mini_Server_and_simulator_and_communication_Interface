[CmdletBinding()]
param()

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$TempDir = Join-Path $ProjectRoot '.cache\detailed_design_docx_tmp'
$OutputDocx = Join-Path $ProjectRoot 'docs\EyalEspressoServerSimulatorDetailedDesign.docx'

# @brief Write a UTF-8 text file without BOM.
# @details Used for intermediate OpenXML part creation before packaging the
# final docx container.
# @param[in] Path Destination file path.
# @param[in] Content File text content.
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

# @brief Create a docx package from named OpenXML parts.
# @details Writes explicit ZIP entries with forward-slash package names so the
# generated file is valid for Word and other OpenXML consumers.
# @param[in] OutputDocx Destination .docx path.
# @param[in] Parts Hashtable mapping package entry names to source file paths.
function New-DocxPackage {
    param(
        [Parameter(Mandatory = $true)]
        [string]$OutputDocx,
        [Parameter(Mandatory = $true)]
        [hashtable]$Parts
    )

    if (Test-Path $OutputDocx) {
        Remove-Item $OutputDocx -Force
    }

    Add-Type -AssemblyName System.IO.Compression
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $fileStream = [System.IO.File]::Open($OutputDocx, [System.IO.FileMode]::CreateNew)
    try {
        $archive = New-Object System.IO.Compression.ZipArchive($fileStream, [System.IO.Compression.ZipArchiveMode]::Create, $false)
        try {
            foreach ($entryName in $Parts.Keys) {
                $entry = $archive.CreateEntry($entryName, [System.IO.Compression.CompressionLevel]::Optimal)
                $entryStream = $entry.Open()
                try {
                    $bytes = [System.IO.File]::ReadAllBytes($Parts[$entryName])
                    $entryStream.Write($bytes, 0, $bytes.Length)
                } finally {
                    $entryStream.Dispose()
                }
            }
        } finally {
            $archive.Dispose()
        }
    } finally {
        $fileStream.Dispose()
    }
}

if (Test-Path $TempDir) {
    Remove-Item $TempDir -Recurse -Force
}

New-Item -ItemType Directory -Force $TempDir | Out-Null
New-Item -ItemType Directory -Force (Join-Path $TempDir '_rels') | Out-Null
New-Item -ItemType Directory -Force (Join-Path $TempDir 'word') | Out-Null

$contentTypes = @"
<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>
"@

$rels = @"
<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>
"@

$documentXml = @"
<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:wpc="http://schemas.microsoft.com/office/word/2010/wordprocessingCanvas" xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006" xmlns:o="urn:schemas-microsoft-com:office:office" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math" xmlns:v="urn:schemas-microsoft-com:vml" xmlns:wp14="http://schemas.microsoft.com/office/word/2010/wordprocessingDrawing" xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" xmlns:w10="urn:schemas-microsoft-com:office:word" xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" xmlns:w14="http://schemas.microsoft.com/office/word/2010/wordml" xmlns:wpg="http://schemas.microsoft.com/office/word/2010/wordprocessingGroup" xmlns:wpi="http://schemas.microsoft.com/office/word/2010/wordprocessingInk" xmlns:wne="http://schemas.microsoft.com/office/word/2006/wordml" xmlns:wps="http://schemas.microsoft.com/office/word/2010/wordprocessingShape" mc:Ignorable="w14 wp14">
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
    <w:sectPr>
      <w:pgSz w:w="12240" w:h="15840"/>
      <w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440" w:header="708" w:footer="708" w:gutter="0"/>
    </w:sectPr>
  </w:body>
</w:document>
"@

$contentTypesPath = Join-Path $TempDir '[Content_Types].xml'
$relsPath = Join-Path $TempDir '_rels\.rels'
$documentPath = Join-Path $TempDir 'word\document.xml'

Write-Utf8File -Path $contentTypesPath -Content $contentTypes
Write-Utf8File -Path $relsPath -Content $rels
Write-Utf8File -Path $documentPath -Content $documentXml

New-DocxPackage -OutputDocx $OutputDocx -Parts @{
    '[Content_Types].xml' = $contentTypesPath
    '_rels/.rels' = $relsPath
    'word/document.xml' = $documentPath
}

Remove-Item $TempDir -Recurse -Force
Get-Item $OutputDocx | Select-Object FullName, Length


