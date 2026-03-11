<#
.SYNOPSIS
Generates the simulator detailed-design `.docx` artifact.

.DESCRIPTION
Builds the detailed design OpenXML package from maintained text content and the
rendered diagram set under `docs\diagrams`, then writes the finished `.docx`
into `docs\`. The script is the canonical machine-generated source for the
detailed design document.
#>
[CmdletBinding()]
param()

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$TempDir = Join-Path $ProjectRoot ".cache\detailed_design_docx_tmp"
$OutputDocx = Join-Path $ProjectRoot "docs\EyalEspressoServerSimulatorDetailedDesign.docx"
$Version = (Get-Content (Join-Path $ProjectRoot "VERSION") -Raw).Trim()
$ReviewedOn = Get-Date -Format 'dd-MMM-yy HH:mm:ss'
$DiagramDir = Join-Path $ProjectRoot "docs\diagrams"
$MaxImageWidthEmu = 6.2 * 914400

# @brief Resolve a writable output path for the generated `.docx`.
# @details Reuses the preferred path when it is unlocked; otherwise it falls
# back to a `.generated.docx` sibling so document generation still succeeds.
# @param[in] PreferredPath Intended output path.
# @return Writable output path.
function Resolve-DocxOutputPath {
    param(
        [Parameter(Mandatory = $true)]
        [string]$PreferredPath
    )

    if (-not (Test-Path $PreferredPath)) {
        return $PreferredPath
    }

    try {
        $stream = [System.IO.File]::Open($PreferredPath, [System.IO.FileMode]::Open, [System.IO.FileAccess]::ReadWrite, [System.IO.FileShare]::None)
        $stream.Dispose()
        return $PreferredPath
    } catch {
        $directory = Split-Path -Parent $PreferredPath
        $fileName = [System.IO.Path]::GetFileNameWithoutExtension($PreferredPath)
        $extension = [System.IO.Path]::GetExtension($PreferredPath)
        return (Join-Path $directory ($fileName + ".generated" + $extension))
    }
}

# @brief Write a UTF-8 text file without BOM.
# @details Used for temporary OpenXML parts before they are packaged into the
# final `.docx` container.
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
# resulting file is valid for Word and other OpenXML consumers.
# @param[in] OutputDocx Destination `.docx` path.
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
        $archive = New-Object System.IO.Compression.ZipArchive(
            $fileStream,
            [System.IO.Compression.ZipArchiveMode]::Create,
            $false
        )
        try {
            foreach ($entryName in $Parts.Keys) {
                $entry = $archive.CreateEntry(
                    $entryName,
                    [System.IO.Compression.CompressionLevel]::Optimal
                )
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

# @brief Convert plain text into one Word paragraph fragment.
# @details Escapes XML-sensitive characters and preserves spaces so generated
# paragraphs remain valid in the document body.
# @param[in] Text Paragraph text.
# @return WordprocessingML paragraph fragment.
function ConvertTo-ParagraphXml {
    param(
        [Parameter(Mandatory = $true)]
        [AllowEmptyString()]
        [string]$Text
    )

    if ([string]::IsNullOrEmpty($Text)) {
        return "<w:p/>"
    }

    $escaped = [System.Security.SecurityElement]::Escape($Text)
    return "<w:p><w:r><w:t xml:space=`"preserve`">$escaped</w:t></w:r></w:p>"
}

# @brief Create a centered italic caption paragraph for an embedded image.
# @details Used immediately before each diagram so the exported document keeps
# figure descriptions aligned with the image they describe.
# @param[in] Caption Figure caption text.
# @return WordprocessingML paragraph fragment.
function New-CaptionParagraphXml {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Caption
    )

    $escaped = [System.Security.SecurityElement]::Escape($Caption)
    return "<w:p><w:pPr><w:jc w:val=`"center`"/></w:pPr><w:r><w:rPr><w:i/></w:rPr><w:t xml:space=`"preserve`">$escaped</w:t></w:r></w:p>"
}

# @brief Create the drawing paragraph for one embedded image.
# @details Emits the minimal WordprocessingML drawing fragment needed to place
# a pre-rendered PNG in the generated document.
# @param[in] RelationshipId Image relationship identifier.
# @param[in] Name Display name for the embedded image.
# @param[in] WidthEmu Render width in EMUs.
# @param[in] HeightEmu Render height in EMUs.
# @param[in] DocPrId Unique drawing-property identifier.
# @return WordprocessingML drawing fragment.
function New-DrawingParagraphXml {
    param(
        [Parameter(Mandatory = $true)]
        [string]$RelationshipId,
        [Parameter(Mandatory = $true)]
        [string]$Name,
        [Parameter(Mandatory = $true)]
        [long]$WidthEmu,
        [Parameter(Mandatory = $true)]
        [long]$HeightEmu,
        [Parameter(Mandatory = $true)]
        [int]$DocPrId
    )

    $escapedName = [System.Security.SecurityElement]::Escape($Name)
    return @"
<w:p>
  <w:pPr><w:jc w:val="center"/></w:pPr>
  <w:r>
    <w:drawing>
      <wp:inline distT="0" distB="0" distL="0" distR="0">
        <wp:extent cx="$WidthEmu" cy="$HeightEmu"/>
        <wp:effectExtent l="0" t="0" r="0" b="0"/>
        <wp:docPr id="$DocPrId" name="$escapedName"/>
        <wp:cNvGraphicFramePr>
          <a:graphicFrameLocks xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" noChangeAspect="1"/>
        </wp:cNvGraphicFramePr>
        <a:graphic xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
          <a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture">
            <pic:pic xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture">
              <pic:nvPicPr>
                <pic:cNvPr id="$DocPrId" name="$escapedName"/>
                <pic:cNvPicPr/>
              </pic:nvPicPr>
              <pic:blipFill>
                <a:blip r:embed="$RelationshipId"/>
                <a:stretch><a:fillRect/></a:stretch>
              </pic:blipFill>
              <pic:spPr>
                <a:xfrm>
                  <a:off x="0" y="0"/>
                  <a:ext cx="$WidthEmu" cy="$HeightEmu"/>
                </a:xfrm>
                <a:prstGeom prst="rect"><a:avLst/></a:prstGeom>
              </pic:spPr>
            </pic:pic>
          </a:graphicData>
        </a:graphic>
      </wp:inline>
    </w:drawing>
  </w:r>
</w:p>
"@
}

# @brief Build the metadata map for all requested embedded images.
# @details Loads each referenced PNG, computes a scaled document size, and
# assigns the relationship identifiers later used in the OpenXML package.
# @param[in] Markers Image marker list extracted from the body content.
# @return Hashtable keyed by image file name.
function Get-ImageMap {
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$Markers
    )

    Add-Type -AssemblyName System.Drawing
    $imageMap = @{}
    $index = 1
    foreach ($marker in $Markers) {
        $parts = $marker -split "\|", 3
        $fileName = $parts[1]
        if ($imageMap.ContainsKey($fileName)) {
            continue
        }

        $filePath = Join-Path $DiagramDir $fileName
        $image = [System.Drawing.Image]::FromFile($filePath)
        try {
            $widthEmu = [long]($image.Width * 9525)
            $heightEmu = [long]($image.Height * 9525)
            if ($widthEmu -gt $MaxImageWidthEmu) {
                $scale = $MaxImageWidthEmu / $widthEmu
                $widthEmu = [long]($widthEmu * $scale)
                $heightEmu = [long]($heightEmu * $scale)
            }

            $imageMap[$fileName] = @{
                FileName = $fileName
                FilePath = $filePath
                RelationshipId = "rId$index"
                DocPrId = $index + 100
                WidthEmu = $widthEmu
                HeightEmu = $heightEmu
                Target = "media/$fileName"
            }
        } finally {
            $image.Dispose()
        }

        $index += 1
    }

    return $imageMap
}

# @brief Convert the body item list into WordprocessingML fragments.
# @details Expands plain text into paragraphs and image markers into caption
# plus drawing fragments using the resolved image metadata.
# @param[in] Items Ordered body content items.
# @param[in] ImageMap Embedded-image metadata.
# @return Concatenated WordprocessingML body fragment.
function ConvertTo-BodyXml {
    param(
        [Parameter(Mandatory = $true)]
        [AllowEmptyString()]
        [AllowEmptyCollection()]
        [string[]]$Items,
        [Parameter(Mandatory = $true)]
        [hashtable]$ImageMap
    )

    $fragments = foreach ($item in $Items) {
        if ($item -match '^\[\[IMAGE\|([^|]+)\|(.+)\]\]$') {
            $fileName = $matches[1]
            $caption = $matches[2]
            $imageInfo = $ImageMap[$fileName]
            New-CaptionParagraphXml -Caption $caption
            New-DrawingParagraphXml `
                -RelationshipId $imageInfo.RelationshipId `
                -Name $fileName `
                -WidthEmu $imageInfo.WidthEmu `
                -HeightEmu $imageInfo.HeightEmu `
                -DocPrId $imageInfo.DocPrId
            "<w:p/>"
            continue
        }

        ConvertTo-ParagraphXml -Text $item
    }

    return ($fragments -join "`n")
}

if (-not (Test-Path $DiagramDir)) {
    throw "Missing diagram directory '$DiagramDir'. Run scripts/generate_transport_diagrams.ps1 first."
}

if (Test-Path $TempDir) {
    Remove-Item $TempDir -Recurse -Force
}

# Start from a clean temporary package tree so regenerated docs do not retain stale parts.
New-Item -ItemType Directory -Force $TempDir | Out-Null
New-Item -ItemType Directory -Force (Join-Path $TempDir "_rels") | Out-Null
New-Item -ItemType Directory -Force (Join-Path $TempDir "word") | Out-Null
New-Item -ItemType Directory -Force (Join-Path $TempDir "word\_rels") | Out-Null
New-Item -ItemType Directory -Force (Join-Path $TempDir "word\media") | Out-Null

$bodyItems = @(
    "Eyal Espresso Server Simulator Detailed Design"
    "Document Status: Working Design Baseline"
    "Project Version Reference: $Version"
    "Reviewed on: $ReviewedOn"
    "Owner: Eyal / Codex"
    ""
    "1. System Context and Planned Split Architecture"
    "Purpose: this document defines the new simulator architecture in which the operator-facing simulator application runs on the PC, but low-level Wi-Fi/TCP transport is delegated to an ESP32-C3-SuperMini attached over USB as a COM port."
    "Deployment Topology:"
    "  PC simulator application -> USB serial COM link -> ESP32-C3-SuperMini transport controller -> Wi-Fi TCP link -> ESP32-S3 client"
    "Architecture Decision: the PC remains the simulator brain, operator UI, scenario engine, and authoritative host-side liveness source. The ESP32-C3 remains a transport bridge plus low-level watchdog and CRC controller."
    "Why This Split Is Preferred: it preserves debuggability and operator visibility on the PC while isolating timing-sensitive low-level transport control in a dedicated microcontroller."
    "[[IMAGE|architecture_transport_split.png|Figure 1. Simulator-side split architecture showing the PC host, USB bridge, ESP32-C3 transport controller, and client link.]]"
    ""
    "2. Responsibility Allocation"
    "2.1 PC Simulator Application Responsibilities"
    "  - operator UI and workflow controls"
    "  - simulator state machine and scenario logic"
    "  - log aggregation and packet trace presentation"
    "  - authoritative HostLiveInteger generation"
    "  - framing and interpretation of low-level commands sent to the ESP32-C3"
    "  - reset and initialize policy decisions after failures"
    "2.2 ESP32-C3-SuperMini Responsibilities"
    "  - own the USB serial session exposed to the PC"
    "  - parse, validate, and emit low-level frames"
    "  - enforce CRC validation before any payload is treated as valid"
    "  - own the Wi-Fi join process and TCP socket establishment toward the client"
    "  - maintain the low-level state machine: reset, initialize, connect, disconnect, error"
    "  - enforce the 100 mSec keep-alive and watchdog behavior"
    "2.3 Client Responsibilities"
    "  - participate in the mirrored low-level transport state machine"
    "  - accept validated payloads only after CRC, sequencing, and liveness checks pass"
    "  - expose higher-level machine protocol behavior above the low-level transport layer"
    ""
    "3. Low-Level State Machine Definition"
    "The lowest communication layer shall implement the same conceptual state model on all participating sides so failure handling is deterministic."
    "States:"
    "  reset"
    "  initialize"
    "  connect"
    "  disconnect"
    "  error"
    "State Semantics:"
    "  reset = hard reset, self-test, counter clear, buffer clear, and transport parameter load"
    "  initialize = prepare the low-level stack from reset parameters but do not yet claim link health"
    "  connect = establish and supervise the active low-level session"
    "  disconnect = perform controlled link shutdown"
    "  error = latch fault state and wait only for reset or initialize"
    "Primary State Diagram:"
    "  reset -> initialize -> connect"
    "  connect -> disconnect -> reset"
    "  connect -> error"
    "  initialize -> error"
    "  disconnect -> error only if controlled shutdown itself fails"
    "  error -> reset"
    "  error -> initialize"
    "[[IMAGE|low_level_state_machine.png|Figure 2. Mirrored low-level transport state machine used by the simulator host, ESP32-C3 bridge, and client.]]"
    "Transport Controller Execution Notes:"
    "  - reset shall clear stale socket ownership and stale COM-port session assumptions"
    "  - initialize shall verify that Wi-Fi credentials, TCP role, target address, and watchdog values are coherent"
    "  - connect shall not forward application data upward until the keep-alive exchange is healthy"
    "  - error shall freeze normal forwarding and preserve the last error reason for the PC host"
    ""
    "3.1 Mirrored Runtime Configuration Defaults"
    "The simulator host shall mirror the same low-level transport defaults used by the client-side communication layer so both repositories describe one coherent interface contract."
    "Default Fields:"
    "  serial_port = COM4"
    "  wifi_ssid = EyalSimulatorAP"
    "  wifi_password = espresso1234"
    "  server_ip = 192.168.4.1"
    "  server_port = 3333"
    "  wifi_connect_timeout_ms = 10000"
    "  tcp_connect_timeout_ms = 3000"
    "  keepalive_period_ms = 100"
    "Simulator Rule: the PC host stores and displays this mirrored configuration even though the ESP32-C3 bridge remains the actual owner of the physical Wi-Fi/TCP link."
    ""
    "4. Standard Low-Level Frame Envelope"
    "Recommendation: use one standard low-level frame envelope with multiple packet types. Do not force every exchange into one giant all-data packet."
    "Reasoning: a stable envelope keeps framing and integrity consistent, while dedicated packet types keep state transitions, watchdog traffic, and application data unambiguous."
    "Recommended Frame Fields:"
    "  SOF"
    "  protocol_version"
    "  message_type"
    "  flags"
    "  payload_length"
    "  host_live_integer"
    "  device_live_integer"
    "  sequence"
    "  payload"
    "  crc"
    "CRC Policy: low-level integrity checking belongs entirely in this envelope layer so the upper-level controller state machine does not need a second checksum mechanism."
    "CRC Recommendation: use CRC16 or CRC32 instead of a weak additive checksum."
    "Liveness Recommendation: HostLiveInteger should be authoritative from the PC host because the main purpose is to prove that the upper-level simulator process is not stalled. The ESP32-C3 should return its own DeviceLiveInteger so liveness can be verified in both directions."
    ""
    "5. Packet Types"
    "RESET packet: requests hard reset and self-test."
    "INITIALIZE packet: requests low-level resource preparation using reset-defined parameters."
    "CONNECT packet: requests active connection establishment or active-session entry."
    "DISCONNECT packet: requests controlled teardown."
    "KEEPALIVE packet: dedicated 100 mSec liveness frame even when no payload changed."
    "DATA packet: carries application-level simulator or controller payload after the low-level link is healthy."
    "ERROR packet: reports latched low-level fault information."
    "ACK packet: acknowledges a command or transport event where a dedicated specialized ACK is sufficient."
    ""
    "6. Flow Diagrams for Each Packet Type"
    "6.1 RESET Packet Flow"
    "  PC Host -> RESET(parameters or parameter profile)"
    "  ESP32-C3 -> enter reset, clear counters, clear partial frames, run self-test"
    "  ESP32-C3 -> RESET_ACK(result, self-test summary)"
    "  PC Host -> if reset succeeded then send initialize"
    "[[IMAGE|packet_reset_flow.png|Figure 3. Simulator RESET packet flow from host command into bridge self-test and acknowledgement.]]"
    ""
    "6.2 INITIALIZE Packet Flow"
    "  PC Host -> INITIALIZE(serial port, Wi-Fi SSID/password, server IP/port, watchdog settings)"
    "  Simulator host -> validate mirrored COM and Wi-Fi/TCP configuration"
    "  ESP32-C3 -> validate configuration and prepare low-level resources"
    "  ESP32-C3 -> INITIALIZE_ACK(status, validation detail)"
    "  PC Host -> if initialize succeeded then send connect"
    "[[IMAGE|packet_initialize_flow.png|Figure 4. Simulator INITIALIZE packet flow for Wi-Fi/TCP preparation and validation.]]"
    ""
    "6.3 CONNECT Packet Flow"
    "  PC Host -> CONNECT(server endpoint reference)"
    "  ESP32-C3 -> establish Wi-Fi-ready state and attempt TCP session"
    "  ESP32-C3 -> CONNECT_ACK(success or failure detail)"
    "  PC Host and ESP32-C3 -> begin periodic keep-alive supervision"
    "[[IMAGE|packet_connect_flow.png|Figure 5. Simulator CONNECT packet flow that enters the active supervised link state.]]"
    ""
    "6.4 DISCONNECT Packet Flow"
    "  PC Host -> DISCONNECT(reason)"
    "  ESP32-C3 -> close low-level session in a controlled manner"
    "  ESP32-C3 -> DISCONNECT_ACK"
    "  PC Host -> return to reset or supervisory idle logic"
    "[[IMAGE|packet_disconnect_flow.png|Figure 6. Simulator DISCONNECT packet flow for intentional transport shutdown.]]"
    ""
    "6.5 KEEPALIVE Packet Flow"
    "  Every 100 mSec PC Host -> KEEPALIVE(host_live_integer incremented)"
    "  ESP32-C3 -> verify host counter advanced within 100 mSec window"
    "  ESP32-C3 -> KEEPALIVE_ACK(device_live_integer, link health, transport status)"
    "  PC Host -> verify response timing and device counter progress"
    "  If expected progress is missing -> both sides transition to error"
    "[[IMAGE|packet_keepalive_flow.png|Figure 7. Simulator KEEPALIVE packet flow with host-owned liveness and bridge status acknowledgement.]]"
    ""
    "6.6 DATA Packet Flow"
    "  PC Host simulator logic -> DATA(application payload)"
    "  ESP32-C3 -> validate frame and CRC"
    "  ESP32-C3 -> transmit payload over Wi-Fi TCP to client"
    "  Client -> respond with DATA or ACK"
    "  ESP32-C3 -> forward validated response to PC host"
    "[[IMAGE|packet_data_flow.png|Figure 8. Simulator DATA packet flow showing bridge-side validation before forwarding.]]"
    ""
    "6.7 ERROR Packet Flow"
    "  ESP32-C3 or client low-level layer detects fault"
    "  Faulting side -> ERROR(error code, state, counters, transport summary)"
    "  Receiving side -> stop trusting link health and enter supervisory recovery"
    "  Recovery command must be reset or initialize"
    "[[IMAGE|packet_error_flow.png|Figure 9. Simulator ERROR packet flow preserving low-level fault context for explicit recovery.]]"
    ""
    "7. Keep-Alive and Watchdog Policy"
    "Timing Rule: every healthy connected session shall exchange keep-alive traffic every 100 mSec."
    "Failure Trigger: if a message was not responded to with the expected liveness progress within 100 mSec, the low-level controller shall move to error."
    "Host Ownership Rule: the host-side simulator shall increment HostLiveInteger. This is preferable because it proves the upper-level PC application is still making forward progress."
    "Device Visibility Rule: the ESP32-C3 should also increment DeviceLiveInteger in responses so the PC can independently detect bridge-side stalls."
    "Why Not Only a Giant Standard Data Packet: if keep-alive is embedded only in full-state data packets, then transport health becomes coupled to application payload frequency. Dedicated keep-alive frames are more reliable."
    ""
    "8. Failure Modes and Respective State Diagrams"
    "A low-level transport fault shall always force a transition into the shared error state before higher-level simulator logic consumes any additional payload."
    "[[IMAGE|failure_modes_overview.png|Figure 10. Simulator failure overview spanning CRC faults, watchdog failures, COM faults, Wi-Fi faults, and TCP session loss.]]"
    "8.1 CRC Failure"
    "Description: received frame fails CRC validation and must not be forwarded upward."
    "Diagram:"
    "  connect -> receive invalid CRC -> error"
    "Recovery:"
    "  error -> reset"
    "  error -> initialize"
    ""
    "8.2 Host Watchdog Failure"
    "Description: HostLiveInteger stops advancing, indicating the PC simulator may be stalled."
    "Diagram:"
    "  connect -> keepalive timeout without host counter progress -> error"
    "Recovery:"
    "  error -> reset or initialize after host recovers"
    ""
    "8.3 Device Watchdog Failure"
    "Description: ESP32-C3 does not provide the expected keep-alive acknowledgement or DeviceLiveInteger progress."
    "Diagram:"
    "  connect -> expected keepalive ack missing -> error"
    "Recovery:"
    "  error -> reset"
    ""
    "8.4 USB COM Failure"
    "Description: the PC can no longer exchange low-level frames with the ESP32-C3 bridge."
    "Diagram:"
    "  reset/initialize/connect -> COM failure -> error"
    "Recovery:"
    "  error -> wait for COM recovery -> reset"
    ""
    "8.4A COM Port Not Found"
    "Description: the simulator host cannot open the configured COM port while preparing the bridge session."
    "Simulator Error Text: COM port not found."
    "Diagram:"
    "  open or initialize -> COM port unavailable -> error"
    "Recovery:"
    "  error -> restore COM availability -> reset"
    ""
    "8.5 Wi-Fi Association Failure"
    "Description: ESP32-C3 cannot join the target Wi-Fi network."
    "Diagram:"
    "  initialize -> Wi-Fi association failure -> error"
    "Recovery:"
    "  error -> initialize with corrected parameters or reset"
    ""
    "8.5A Configured AP Offline or Not Visible"
    "Description: the mirrored Wi-Fi configuration indicates an unavailable AP or bridge-side validation reports that the target AP is not visible."
    "Simulator Error Text: Wi-Fi AP is offline."
    "Diagram:"
    "  initialize -> validate mirrored Wi-Fi config -> AP unavailable -> error"
    "Recovery:"
    "  error -> correct RF environment or Wi-Fi configuration -> reset -> initialize"
    ""
    "8.5B TCP Server Not Found or Not Listening"
    "Description: the bridge-side connect path cannot reach the configured server endpoint as an active listener."
    "Simulator Error Text: TCP server not found."
    "Diagram:"
    "  connect -> TCP listener unavailable -> error"
    "Recovery:"
    "  error -> restore listener or correct endpoint -> initialize -> connect"
    ""
    "8.5C Generic Unknown Transport Failure"
    "Description: a transport stage fails without enough evidence to classify the fault as COM, Wi-Fi AP, or TCP listener specific."
    "Simulator Error Text: generic unknown failure."
    "Diagram:"
    "  initialize/connect -> stage-specific failure without precise classification -> error"
    "Recovery:"
    "  error -> review stage detail -> reset or initialize"
    ""
    "8.6 TCP Session Loss"
    "Description: Wi-Fi may still be up while the TCP socket to the client is gone."
    "Diagram:"
    "  connect -> socket loss -> error"
    "Recovery:"
    "  error -> initialize -> connect"
    ""
    "8.7 Malformed Packet or Unsupported Version"
    "Description: parser rejects packet structure before payload is considered valid."
    "Diagram:"
    "  initialize/connect -> parser reject -> error"
    "Recovery:"
    "  error -> reset after protocol review"
    ""
    "8.8 Intentional Disconnect"
    "Description: upper-level host requests controlled shutdown."
    "Diagram:"
    "  connect -> disconnect -> reset"
    "Recovery:"
    "  reset -> initialize -> connect when commanded"
    ""
    "9. Simulator Software Module Allocation"
    "Current Runtime Stack: Python, FastAPI, pyserial, uvicorn."
    "Current PC-Side Code Boundaries:"
    "  server/app.py = FastAPI entry point"
    "  server/api/routes.py = API endpoints used by the operator UI"
    "  server/sim/controller_state.py = simulator-side state and telemetry model"
    "  server/sim/link_state_machine.py = mirrored low-level COM + Wi-Fi/TCP state model and error mapping"
    "  server/transport/serial_link.py = logical transport ownership model"
    "Planned Firmware Boundary: the ESP32-C3 firmware should become a separate low-level transport project rather than being hidden inside the PC simulator application."
    ""
    "10. Environment and Local Run Procedure"
    "Local Environment Setup: create a virtual environment with python -m venv .venv."
    "Dependency Install: .\.venv\Scripts\python.exe -m pip install -r requirements.txt pytest"
    "Normal Run Command: ./scripts/run_simulator.ps1"
    "Development Run Command: ./scripts/run_simulator.ps1 -Reload"
    "Default Access URL: http://127.0.0.1:8000"
    "Backend Launch Design:"
    "  - use a deterministic repo-local launcher script or service wrapper"
    "  - prefer the repository virtual environment interpreter before a global Python installation"
    "  - capture stdout and stderr to logs or keep them visible in the supervising console"
    "  - require a concrete readiness signal such as GET /health before opening the UI"
    "  - supervise the process with a stable host if it must outlive the initiating shell"
    "  - separate application correctness from editor, sandbox, or task-runner lifetime"
    "Preferred Uvicorn Launch Contract:"
    "  - launch scripts/run_simulator.ps1 from the repository root"
    "  - let the script resolve .\.venv\Scripts\python.exe when available"
    "  - start uvicorn as server.app:app on 127.0.0.1:8000"
    "  - verify readiness with GET /health expecting 200 OK and {""status"":""ok""}"
    "  - open the browser only after readiness succeeds"
    "  - bypass stale browser state with a cache-busting query string when opening the UI"
    "Operational Note: if a foreground uvicorn launch works but a detached tool-hosted launch dies immediately, classify the problem as process-hosting automation first, not as a backend application defect."
    "Helper Script Inventory:"
    "  - scripts/run_simulator.ps1 = primary repo-local simulator launcher that resolves the repository root, prefers the local .venv interpreter, and starts uvicorn"
    "  - scripts/run_simulator.bat = Windows batch wrapper that forwards arguments into scripts/run_simulator.ps1 with execution-policy bypass"
    "  - scripts/setup_idf_env.ps1 = prepares the local ESP-IDF shell environment for firmware-side commands"
    "  - scripts/idfw.ps1 = PowerShell helper that forwards ESP-IDF commands through the local environment activation flow"
    "  - scripts/idfw.cmd = cmd wrapper for the ESP-IDF PowerShell launcher so Windows shells can invoke firmware workflows consistently"
    "  - scripts/monitor_capture.ps1 = fallback serial capture helper that reads the target port without reset for bounded log collection"
    "  - scripts/play_build_success_sound.ps1 = one-shot celebration sound helper for successful verification cycles"
    "  - scripts/play_wait_sound.ps1 = one-shot wait-notification playback helper"
    "  - scripts/start_wait_sound.ps1 = starts the repeating wait-sound worker and records its process handle"
    "  - scripts/stop_wait_sound.ps1 = stops the repeating wait-sound worker using the recorded handle"
    "  - scripts/generate_requirements_docx.ps1 = generates the requirements-and-design .docx artifact from repo-local OpenXML content"
    "  - scripts/generate_detailed_design_docx.ps1 = generates this detailed design .docx artifact and embeds the maintained diagram images"
    "  - scripts/generate_transport_diagrams.ps1 = renders the maintained transport architecture diagrams used by the design set"
    ""
    "11. Simulator UI State and Command Feedback"
    "Command Button Rule: simulator command buttons use blue as the default unpressed color."
    "Pressed/In-Progress Rule: when a command button is pressed it turns gray and looks pressed while the related command or state transition is still in progress."
    "Completion Rule: after the related state/action finishes, the command button returns to the default blue unpressed state."
    "Machine State Color Rules:"
    "  dark blue = inactive / default after reset"
    "  blinking green = in progress"
    "  red = finished with failure"
    "  light green = finished with success"
    "Grouping Rule: machine-state indication shall be shown in a dedicated titled group box named Machine State so state reporting remains visually separate from operator commands."
    "Logger Rule: the logger remains a separate rolling history surface and is not itself a machine-state indicator."
    ""
    "12. Notes"
    "This design intentionally separates low-level transport supervision from upper-level simulator behavior so watchdog correctness, CRC validation, and failure recovery remain auditable."
    "The packet and state diagrams above are meant to be reviewed as the source-of-truth baseline before firmware and client-side low-level code are expanded further."
)

$imageMarkers = $bodyItems | Where-Object { $_ -match '^\[\[IMAGE\|' } | Select-Object -Unique
$imageMap = Get-ImageMap -Markers $imageMarkers
$bodyXml = ConvertTo-BodyXml -Items $bodyItems -ImageMap $imageMap
$ResolvedOutputDocx = Resolve-DocxOutputPath -PreferredPath $OutputDocx

$contentTypes = @'
<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Default Extension="png" ContentType="image/png"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>
'@

$packageRels = @'
<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>
'@

$documentRelationships = @(
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
)
foreach ($imageInfo in $imageMap.Values | Sort-Object RelationshipId) {
    $documentRelationships += "  <Relationship Id=`"$($imageInfo.RelationshipId)`" Type=`"http://schemas.openxmlformats.org/officeDocument/2006/relationships/image`" Target=`"$($imageInfo.Target)`"/>"
}
$documentRelationships += '</Relationships>'
$documentRelationshipsXml = $documentRelationships -join "`n"

$documentXml = @"
<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:wpc="http://schemas.microsoft.com/office/word/2010/wordprocessingCanvas" xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006" xmlns:o="urn:schemas-microsoft-com:office:office" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math" xmlns:v="urn:schemas-microsoft-com:vml" xmlns:wp14="http://schemas.microsoft.com/office/word/2010/wordprocessingDrawing" xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" xmlns:w10="urn:schemas-microsoft-com:office:word" xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" xmlns:w14="http://schemas.microsoft.com/office/word/2010/wordml" xmlns:wpg="http://schemas.microsoft.com/office/word/2010/wordprocessingGroup" xmlns:wpi="http://schemas.microsoft.com/office/word/2010/wordprocessingInk" xmlns:wne="http://schemas.microsoft.com/office/word/2006/wordml" xmlns:wps="http://schemas.microsoft.com/office/word/2010/wordprocessingShape" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture" mc:Ignorable="w14 wp14">
  <w:body>
$bodyXml
    <w:sectPr>
      <w:pgSz w:w="12240" w:h="15840"/>
      <w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440" w:header="708" w:footer="708" w:gutter="0"/>
    </w:sectPr>
  </w:body>
</w:document>
"@

$contentTypesPath = Join-Path $TempDir "[Content_Types].xml"
$packageRelsPath = Join-Path $TempDir "_rels\.rels"
$documentPath = Join-Path $TempDir "word\document.xml"
$documentRelsPath = Join-Path $TempDir "word\_rels\document.xml.rels"

Write-Utf8File -Path $contentTypesPath -Content $contentTypes
Write-Utf8File -Path $packageRelsPath -Content $packageRels
Write-Utf8File -Path $documentPath -Content $documentXml
Write-Utf8File -Path $documentRelsPath -Content $documentRelationshipsXml

$parts = @{
    "[Content_Types].xml" = $contentTypesPath
    "_rels/.rels" = $packageRelsPath
    "word/document.xml" = $documentPath
    "word/_rels/document.xml.rels" = $documentRelsPath
}

foreach ($imageInfo in $imageMap.Values) {
    $parts["word/media/$($imageInfo.FileName)"] = $imageInfo.FilePath
}

New-DocxPackage -OutputDocx $ResolvedOutputDocx -Parts $parts

Remove-Item $TempDir -Recurse -Force
Get-Item $ResolvedOutputDocx | Select-Object FullName, Length
