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
$FirmwareVersion = (Get-Content (Join-Path $ProjectRoot "firmware\esp32c3_bridge\VERSION") -Raw).Trim()
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
    "Owner: Eyal / Claude"
    ""
    "Revision History"
    "v0.2.1 - First working version with CLAUDE"
    "  - Bridge INITIALIZE handler verified: requires data_ver=1 in payload (BRIDGE_REALTIME_DATA_INTERFACE_VERSION = 1)"
    "  - Bridge KEEPALIVE handler verified: requires rver=1 in client response payload"
    "  - Bridge DATA frame confirmed: 170-byte binary payload (version u32 LE, sequence u32 LE, value_bytes u16 LE = 160, 20 x double temperatures)"
    "  - BRIDGE_FRAME_MAX_PAYLOAD = 256; client raised COMMUNICATION_FRAME_MAX_PAYLOAD to match"
    "  - Transport fully operational: RESET -> INITIALIZE -> CONNECT -> KEEPALIVE cycle sustained continuously"
    ""
    "1. System Context and Planned Split Architecture"
    "Purpose: this document defines the new simulator architecture in which the operator-facing simulator application runs on the PC, but low-level Wi-Fi/TCP transport is delegated to an ESP32-C3-SuperMini attached over USB as a COM port."
    "Deployment Topology:"
    "  PC simulator application -> USB serial COM link -> ESP32-C3-SuperMini transport controller -> Wi-Fi TCP link -> ESP32-S3 client"
    "Architecture Decision: the PC remains the simulator brain, operator UI, scenario engine, and supervisory control surface. The ESP32-C3 remains the authoritative low-level transport controller, including live-integer ownership, keepalive/watchdog enforcement, and CRC validation."
    "Why This Split Is Preferred: it preserves debuggability and operator visibility on the PC while isolating timing-sensitive low-level transport control in a dedicated microcontroller."
    "[[IMAGE|architecture_transport_split.png|Figure 1. Simulator-side split architecture showing the PC host, USB bridge, ESP32-C3 transport controller, and client link.]]"
    ""
    "2. Responsibility Allocation"
    "2.1 PC Simulator Application Responsibilities"
    "  - operator UI and workflow controls"
    "  - simulator state machine and scenario logic"
    "  - log aggregation and packet trace presentation"
    "  - framing and interpretation of low-level commands sent to the ESP32-C3"
    "  - mirrored state presentation, logging, and operator controls"
    "  - COM-port ownership, forced COM release, and USB session lifetime control"
    "  - mirrored configuration validation before INITIALIZE is sent"
    "  - reset and initialize policy decisions after failures reported by the bridge"
    "  - sequential keepalive-failure counting and ConnectionFault latching into wait_for_com_reset"
    "2.2 ESP32-C3-SuperMini Responsibilities"
    "  - own the USB serial session exposed to the PC"
    "  - parse, validate, and emit low-level frames"
    "  - enforce CRC validation before any payload is treated as valid"
    "  - own the Wi-Fi join process and TCP socket establishment toward the client"
    "  - maintain the low-level state machine: reset, initialize, connect, keepalive, and bridge fault reporting"
    "  - own ServerLiveInteger and ClientLiveInteger during the active session"
    "  - zero both counters every time connect is entered"
    "  - enforce the bridge keepalive cadence (default 300 mSec) with a 450 mSec response window and timeout-driven retries"
    "2.3 Client Responsibilities"
    "  - participate in the mirrored low-level transport state machine"
    "  - accept validated payloads only after CRC, sequencing, and liveness checks pass"
    "  - expose higher-level machine protocol behavior above the low-level transport layer"
    ""
    "2.4 Current Implemented Split: PC Simulator Runtime"
    "Current Main Files:"
    "  - server/app.py = FastAPI entry point that serves /health, /api/app-info, the API router, and the browser UI shell"
    "  - server/api/routes.py = operator and test API endpoints that call the low-level runtime actions"
    "  - server/sim/link_state_machine.py = current host-side mirror of authoritative bridge state"
    "  - server/transport/serial_link.py = one-owner serial manager for the COM endpoint"
    "  - server/transport/frame_codec.py = framed packet encoder/decoder with CRC16"
    "Implemented TopLayer Server States:"
    "  - reset"
    "  - initialize"
    "  - connect"
    "  - keepalive_server_send"
    "  - keepalive_client_return"
    "  - error"
    "Main Server Runtime Functions:"
    "  - LinkRuntime.configure_port() = update the active serial port target"
    "  - LinkRuntime.configure_transport() = update mirrored serial, Wi-Fi, TCP, and watchdog configuration"
    "  - LinkRuntime.open_transport() = open and own the configured COM endpoint"
    "  - LinkRuntime.close_transport() = release the configured COM endpoint and clear transport-ready flags"
    "  - LinkRuntime.force_release_transport() = hard COM-port release helper for likely external holders"
    "  - LinkRuntime.toggle_wifi_enabled() = operator test hook for bridge SoftAP availability"
    "  - LinkRuntime.reset() = clear mirrored host state, send RESET, and arm automatic initialize progression"
    "  - LinkRuntime.initialize() = validate mirrored configuration, send INITIALIZE, and schedule the automatic CONNECT stage"
    "  - LinkRuntime.send_keepalive() = monitor-only API that reports bridge-owned keepalive state"
    "  - LinkRuntime.send_data() = transmit DATA only after the bridge has reported keepalive-ready state"
    "  - LinkRuntime.get_snapshot() = return the current operator-visible runtime snapshot"
    "  - SerialLinkManager.open_port() / close_port() / send_frame() = the only code paths allowed to touch the serial object"
    "Current UI Integration:"
    "  - server/ui/index.html = browser operator dashboard that drives Screen 1 through Screen 5, including telemetry and delay diagnostics, through the API routes"
    ""
    "2.5 Current Implemented Split: ESP32-C3 Transport Controller Firmware"
    "Current Main Files:"
    "  - firmware/esp32c3_bridge/main/bridge_main.c = current ESP32-C3 transport-controller baseline"
    "Implemented Bridge States:"
    "  - BRIDGE_STATE_RESET"
    "  - BRIDGE_STATE_INITIALIZE"
    "  - BRIDGE_STATE_CONNECT"
    "Implementation Note: legacy bridge enums still exist internally, but the active host-visible transport contract is now TopLayer reset -> initialize -> connect -> keepalive_server_send -> keepalive_client_return_server_send <-> keepalive_client_return -> error with explicit reset recovery."
    "Implemented Bridge Message Types:"
    "  - BRIDGE_MESSAGE_RESET"
    "  - BRIDGE_MESSAGE_INITIALIZE"
    "  - BRIDGE_MESSAGE_CONNECT"
    "  - BRIDGE_MESSAGE_DISCONNECT"
    "  - BRIDGE_MESSAGE_KEEPALIVE"
    "  - BRIDGE_MESSAGE_ERROR"
    "  - BRIDGE_MESSAGE_ACK"
    "  - BRIDGE_MESSAGE_DATA"
    "Main Bridge Functions:"
    "  - app_main() = USB-Serial/JTAG polling loop that reads frames, services TCP transport, and enforces the bridge watchdog"
    "  - bridge_transport_init() = install and configure the USB-Serial/JTAG transport used as the COM endpoint"
    "  - bridge_parse_frame() = validate framing, payload length, and CRC before accepting a request"
    "  - bridge_handle_frame() = move the bridge state machine and emit the matching ACK or KEEPALIVE response"
    "  - bridge_send_frame() = serialize and transmit one framed response back to the PC simulator host"
    "  - bridge_service_transport_watchdog() = authoritative keepalive supervision and stale-session teardown on the bridge"
    "  - bridge_crc16_ccitt() = shared integrity algorithm used for bridge-side frame validation and emission"
    "Current Bridge Scope Limitation:"
    "  - the ESP32-C3 firmware is now the low-level transport authority, but higher-level simulator scenarios and UI orchestration still live on the PC host"
    ""
    "3. Two-Layer State Machine Definition"
    "The transport stack is split into BottomLayer reliability and TopLayer supervision so failure handling is deterministic."
    "TopLayer States:"
    "  reset"
    "  initialize"
    "  connect"
    "  keepalive_server_send"
    "  keepalive_client_return"
    "  error"
    "TopLayer State Semantics:"
    "  reset = hard reset, zero all counters, clear buffers, and arm initialize"
    "  initialize = load mirrored communication constants for both layers"
    "  connect = BottomLayer establish and wait for bridge-proven keepalive readiness"
    "  keepalive_server_send = bridge sends one authoritative keepalive request and starts response timeout window"
    "  keepalive_client_return = bridge validates one client return and advances to the next send cycle"
    "  error = blocking fault state; explicit reset is required before reconnect"
    "BottomLayer Rules:"
    "  BottomLayer uses standard Wi-Fi/TCP auto-connect best practice with checksum validation and up to 3 retries"
    "  BottomLayer retry triggers include link loss, reconnect failure, and checksum validation failure"
    "TopLayer Rules:"
    "  TopLayer detects sequential-communication loss and async freeze/watchdog faults"
    "  TopLayer enters error if BottomLayer retry count reaches 3 OR TopLayer failure count reaches 3"
    "Connect-to-KeepAlive Rule:"
    "  connect completion moves to keepalive_server_send, then each cycle alternates keepalive_server_send and keepalive_client_return"
    "Primary State Diagram:"
    "  reset -> initialize -> connect -> keepalive_server_send -> keepalive_client_return"
    "  connect -> error on BottomLayerRetries==3 OR TopLayerFailures==3"
    "  keepalive_server_send or keepalive_client_return -> error on async freeze or sequential communication loss"
    "  error -> reset only by explicit Reset Communication"
    "Design Diagram Source: docs/architecture/top_bottom_layer_state_machine.mmd"
    "[[IMAGE|low_level_state_machine.png|Figure 2. Host-visible low-level transport state machine, with bridge-side timing ownership.]]"
    "Transport Controller Execution Notes:"
    "  - reset shall clear stale socket ownership and stale COM-port session assumptions, then arm automatic initialize progression"
    "  - initialize shall verify that Wi-Fi credentials, TCP role, target address, and watchdog values are coherent before automatic connect"
    "  - connect shall zero both live integers at the bridge before the first keepalive of a new session"
    "  - the Python host shall not derive transport watchdog failures from browser snapshot timing"
    "  - keepalive shall not forward application data upward until the bridge reports a healthy exchange"
    ""
    "3.1 Mirrored Runtime Configuration Defaults"
    "The simulator host shall mirror the same low-level transport defaults used by the client-side communication layer so both repositories describe one coherent interface contract."
    "Default Fields:"
    "  serial_port = COM4"
    "  wifi_ssid = EyalSimulatorAP (advertised as a visible SoftAP by the ESP32-C3 bridge)"
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
    "Liveness Recommendation: ServerLiveInteger and ClientLiveInteger are authoritative from the ESP32-C3 bridge/client transport exchange. The Python simulator host mirrors those values and does not invent them."
    ""
    "5. Packet Types"
    "RESET packet: requests hard reset and self-test."
    "INITIALIZE packet: requests low-level resource preparation using reset-defined parameters."
    "CONNECT packet: requests active connection establishment or active-session entry."
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
    "  PC Host runtime -> if reset succeeded then automatically enter initialize"
    "[[IMAGE|packet_reset_flow.png|Figure 3. Simulator RESET packet flow from host command into bridge self-test and acknowledgement.]]"
    ""
    "6.2 INITIALIZE Packet Flow"
    "  PC Host -> INITIALIZE(serial port, Wi-Fi SSID/password, server IP/port, watchdog settings)"
    "  Simulator host -> validate mirrored COM and Wi-Fi/TCP configuration"
    "  ESP32-C3 -> validate configuration and prepare low-level resources"
    "  ESP32-C3 -> INITIALIZE_ACK(status, validation detail)"
    "  PC Host runtime -> if initialize succeeded then automatically enter connect"
    "[[IMAGE|packet_initialize_flow.png|Figure 4. Simulator INITIALIZE packet flow for Wi-Fi/TCP preparation and validation.]]"
    ""
    "6.3 CONNECT Packet Flow"
    "  PC Host -> CONNECT(server endpoint reference)"
    "  ESP32-C3 -> zero ServerLiveInteger and ClientLiveInteger and establish Wi-Fi-ready state"
    "  ESP32-C3 -> CONNECT_ACK(success or failure detail)"
    "  ESP32-C3 -> if a TCP client is already present, immediately mirror client_connected and the first KEEPALIVE"
    "[[IMAGE|packet_connect_flow.png|Figure 5. Simulator CONNECT packet flow that enters the active supervised link state.]]"
    ""
    "6.4 KEEPALIVE Packet Flow"
    "  Every 100 mSec ESP32-C3 -> KEEPALIVE(ServerLiveInteger, ClientLiveInteger)"
    "  ESP32-S3 client -> validate ServerLiveInteger and increment ClientLiveInteger"
    "  ESP32-S3 client -> KEEPALIVE(ClientLiveInteger incremented)"
    "  ESP32-C3 -> verify the returned ClientLiveInteger and advance ServerLiveInteger"
    "  Python simulator host -> mirror the authoritative bridge counters over USB"
    "[[IMAGE|packet_keepalive_flow.png|Figure 6. Simulator KEEPALIVE packet flow with bridge-owned liveness and host-side mirroring.]]"
    ""
    "6.5 DATA Packet Flow"
    "  ESP32-C3 bridge sends binary DATA frames to client during keepalive states"
    "  DATA Frame Binary Layout (170 bytes):"
    "    bytes 0..3   = version u32 LE = BRIDGE_REALTIME_DATA_INTERFACE_VERSION (1)"
    "    bytes 4..7   = data_sequence u32 LE (increments each frame)"
    "    bytes 8..9   = value_bytes u16 LE = 160"
    "    bytes 10..169 = 20 x double temperature channels (stub: 85.0 + index + sequence*0.01)"
    "  BRIDGE_FRAME_MAX_PAYLOAD = 256; client COMMUNICATION_FRAME_MAX_PAYLOAD must be >= 170 to accept DATA frames"
    "  PC Host simulator logic -> DATA(application payload)"
    "  ESP32-C3 -> validate frame and CRC"
    "  ESP32-C3 -> transmit payload over Wi-Fi TCP to client"
    "  Client -> respond with DATA or ACK"
    "  ESP32-C3 -> forward validated response to PC host"
    "[[IMAGE|packet_data_flow.png|Figure 7. Simulator DATA packet flow showing bridge-side validation before forwarding.]]"
    ""
    "6.6 ERROR Packet Flow"
    "  ESP32-C3 bridge or client low-level layer detects fault"
    "  Faulting side -> ERROR(error code, state, counters, transport summary)"
    "  Python host -> retry through connect for bridge-reported keepalive loss or latch wait_for_com_reset for blocking COM/runtime faults"
    "  Recovery command must be reset once wait_for_com_reset is reached"
    "[[IMAGE|packet_error_flow.png|Figure 8. Simulator ERROR packet flow preserving low-level fault context for explicit recovery.]]"
    ""
    "7. Keep-Alive and Watchdog Policy"
    "Timing Rule: every healthy connected session shall exchange keep-alive traffic every 100 mSec."
    "Failure Trigger: if the ESP32-C3 bridge does not receive the expected client keepalive progress within the watchdog window, it shall report keepalive_supervision_lost upstream and close the stale TCP session."
    "Counter Ownership Rule: the bridge zeros both counters when connect begins, then ServerLiveInteger starts on the bridge side and ClientLiveInteger advances only after the client validates the received server value."
    "Host Visibility Rule: the Python simulator host mirrors bridge-reported counters and faults; it does not derive keepalive loss from UI polling cadence."
    "Why Not Only a Giant Standard Data Packet: if keep-alive is embedded only in full-state data packets, then transport health becomes coupled to application payload frequency. Dedicated keep-alive frames are more reliable."
    ""
    "8. Failure Modes and Respective State Diagrams"
    "A low-level transport fault shall be detected first by the ESP32-C3 bridge when it relates to the active TCP keepalive session. The Python host mirrors those faults and applies operator recovery policy."
    "[[IMAGE|failure_modes_overview.png|Figure 9. Simulator failure overview showing bridge-owned transport detection and host-side recovery policy.]]"
    "8.1 CRC Failure"
    "Description: received frame fails CRC validation and must not be forwarded upward."
    "Diagram:"
    "  connect or keepalive -> receive invalid CRC -> reject frame and retry transport progression"
    "Recovery:"
    "  automatic retry through connect, then explicit reset after threshold if required"
    ""
    "8.2 Bridge Keepalive Watchdog Failure"
    "Description: the ESP32-C3 bridge does not receive the expected client keepalive progression within the watchdog window."
    "Diagram:"
    "  keepalive -> bridge watchdog timeout -> host retry through connect"
    "Recovery:"
    "  repeated bridge watchdog failures -> wait_for_com_reset -> reset after operator action"
    ""
    "8.3 Device-Side Keepalive Failure"
    "Description: the ESP32-S3 client does not provide the expected ClientLiveInteger progression."
    "Diagram:"
    "  keepalive -> bridge observes missing client forward progress -> bridge reports keepalive_supervision_lost -> host retry through connect"
    "Recovery:"
    "  automatic retry until threshold, then wait_for_com_reset -> reset"
    ""
    "8.4 USB COM Failure"
    "Description: the PC can no longer exchange low-level frames with the ESP32-C3 bridge."
    "Diagram:"
    "  reset/initialize/connect/keepalive -> COM failure -> wait_for_com_reset"
    "Recovery:"
    "  restore COM availability -> reset"
    ""
    "8.4A COM Port Not Found"
    "Description: the simulator host cannot open the configured COM port while preparing the bridge session."
    "Simulator Error Text: COM port not found."
    "Diagram:"
    "  open or initialize -> COM port unavailable -> wait_for_com_reset"
    "Recovery:"
    "  restore COM availability -> reset"
    ""
    "8.5 Wi-Fi Association Failure"
    "Description: the bridge-side connect path cannot establish the required Wi-Fi/TCP transport preconditions."
    "Diagram:"
    "  initialize or connect -> bridge-side Wi-Fi/TCP failure -> retry through connect"
    "Recovery:"
    "  automatic retry or explicit reset after operator action"
    ""
    "8.5A Configured AP Offline or Not Visible"
    "Description: the mirrored Wi-Fi configuration indicates an unavailable AP or bridge-side validation reports that the target AP is not visible."
    "Simulator Error Text: Wi-Fi AP is offline."
    "Diagram:"
    "  initialize -> validate mirrored Wi-Fi config -> AP unavailable -> wait_for_com_reset"
    "Recovery:"
    "  correct RF environment or Wi-Fi configuration -> reset -> initialize"
    ""
    "8.5B TCP Server Not Found or Not Listening"
    "Description: the bridge-side connect path cannot reach the configured server endpoint as an active listener."
    "Simulator Error Text: TCP server not found."
    "Diagram:"
    "  connect -> TCP listener unavailable -> retry through connect"
    "Recovery:"
    "  restore listener or correct endpoint -> initialize -> connect"
    ""
    "8.5C Generic Unknown Transport Failure"
    "Description: a transport stage fails without enough evidence to classify the fault as COM, Wi-Fi AP, or TCP listener specific."
    "Simulator Error Text: generic unknown failure."
    "Diagram:"
    "  initialize/connect -> stage-specific failure without precise classification -> retry or wait_for_com_reset depending on the failing stage"
    "Recovery:"
    "  review stage detail -> reset or initialize"
    ""
    "8.6 TCP Session Loss"
    "Description: Wi-Fi may still be up while the TCP socket to the client is gone."
    "Diagram:"
    "  keepalive -> socket loss -> retry through connect"
    "Recovery:"
    "  initialize -> connect"
    ""
    "8.7 Malformed Packet or Unsupported Version"
    "Description: parser rejects packet structure before payload is considered valid."
    "Diagram:"
    "  initialize/connect/keepalive -> parser reject -> drop frame and retry transport progression"
    "Recovery:"
    "  automatic retry or reset after protocol review"
    ""
    "8.8 Reset-Only Error Recovery"
    "Description: any latched wait_for_com_reset condition may be cleared only by reset."
    "Diagram:"
    "  wait_for_com_reset -> reset -> initialize -> connect"
    "Recovery:"
    "  no direct initialize-from-error path is allowed"
    ""
    "9. Simulator Software Module Allocation"
    "Current Runtime Stack: Python, FastAPI, pyserial, uvicorn."
    "Current PC-Side Code Boundaries:"
    "  ServerInterface/frame_codec.py = shared Python reference implementation of the portable transport framing rules"
    "  ServerInterface/native/include/server_interface/*.hpp and c_api.h = portable native protocol headers intended for MCU reuse"
    "  ServerInterface/native/src/protocol.cpp = native framing and CRC implementation intended for later STM32 reuse through the same C API"
    "  server/app.py = FastAPI entry point"
    "  server/api/routes.py = API endpoints used by the operator UI"
    "  server/sim/controller_state.py = simulator-side state and telemetry model"
    "  server/sim/link_state_machine.py = mirrored low-level COM + Wi-Fi/TCP state model and error mapping"
    "  server/transport/serial_link.py = logical transport ownership model"
    "  server/transport/frame_codec.py = compatibility shim that re-exports the extracted ServerInterface Python reference module"
    "Shared-Interface Rule: transport framing, CRC, packet enums, and later low-level state-machine entry points shall move into ServerInterface first so the PC server and future STM32 firmware can exercise the same interface contract."
    "Planned Firmware Boundary: the ESP32-C3 firmware should become a separate low-level transport project rather than being hidden inside the PC simulator application."
    ""
    "10. Environment and Local Run Procedure"
    "Local Environment Setup: create a virtual environment with python -m venv .venv."
    "Dependency Install: .\.venv\Scripts\python.exe -m pip install -r requirements.txt pytest"
    "Normal Run Command: ./scripts/run_simulator.ps1"
    "Development Run Command: ./scripts/run_simulator.ps1 -Reload"
    "Default Access URL: http://127.0.0.1:8000"
    "Repository Version Tree:"
    "  - Application Version = $Version from repository root VERSION"
    "  - Firmware Version = $FirmwareVersion from firmware/esp32c3_bridge/VERSION"
    "Version Tree Rule: application and firmware versions use the same X.Y.Z policy but remain independently tracked artifacts inside one repository version tree."
    "Runtime Version Baseline:"
    "  - Python = 3.13.3"
    "  - fastapi = 0.135.1"
    "  - uvicorn = 0.41.0"
    "  - pyserial = 3.5"
    "Versioning Rule: these runtime versions define the local development baseline for the manual simulator launcher and installation verification flow."
    "Installation Verification Design:"
    "  - the manual launcher shall verify the required local runtime components inside the single visible launcher host before it attempts any simulator startup step"
    "  - the required development-baseline runtime shall match the Runtime Version Baseline listed in this section using the repository-local .venv"
    "  - if any required component is missing or its version differs from the development baseline, show a popup listing the gaps and stop the launch"
    "  - do not continue into backend startup when installation verification fails"
    "Backend Launch Design:"
    "  - use a deterministic repo-local launcher script or service wrapper"
    "  - prefer the repository virtual environment interpreter before a global Python installation"
    "  - before launching uvicorn, inspect any existing listener already bound to the requested simulator port and only replace it when it is positively identified as this simulator backend"
    "  - prefer graceful shutdown before forced termination when replacing an existing simulator listener"
    "  - capture stdout and stderr to logs or keep them visible in the supervising console"
    "  - require a concrete readiness signal such as GET /health before opening the UI"
    "  - supervise the process with a stable host if it must outlive the initiating shell"
    "  - separate application correctness from editor, sandbox, or task-runner lifetime"
    "Preferred Uvicorn Launch Contract:"
    "  - scripts/run_simulator.bat shall open one temporary PowerShell session for the standard-user launch path and close it after backend readiness and the browser prompt complete"
    "  - scripts/launch_simulator_ui.ps1 shall own installation verification, backend readiness waiting, and the browser-open prompt inside that same launcher host"
    "  - launch scripts/run_simulator.ps1 from the repository root for backend-only startup logic"
    "  - let the script resolve .\.venv\Scripts\python.exe when available"
    "  - stop a pre-existing process only when the launcher can verify from process metadata and command line that the listener belongs to this simulator backend"
    "  - refuse startup rather than kill an unrelated service that happens to own the target port"
    "  - attempt normal process termination first, then force-stop only if the old simulator backend does not exit in time"
    "  - the manual launcher shall start the backend as a Python process directly rather than opening a second visible PowerShell host"
    "  - start uvicorn as server.app:app on 127.0.0.1:8000"
    "  - verify readiness with GET /health expecting 200 OK and {""status"":""ok""}"
    "  - open the browser only after readiness succeeds"
    "  - for the manual batch launcher, show a Yes/No prompt after backend readiness asking whether to open the simulator in a fresh browser session with a cache-busting URL"
    "  - the startup splash screen shall show the application name, simulator application version, bridge firmware version, and backend build timestamp for 5 seconds"
    "  - bypass stale browser state with a cache-busting query string when opening the UI"
    "Operational Note: if a foreground uvicorn launch works but a detached tool-hosted launch dies immediately, classify the problem as process-hosting automation first, not as a backend application defect."
    "Helper Script Inventory:"
    "  - scripts/run_simulator.ps1 = primary repo-local simulator launcher that resolves the repository root, prefers the local .venv interpreter, safely replaces only verified stale simulator listeners on the target port, and starts uvicorn"
    "  - scripts/verify_simulator_installation.ps1 = shared manual-launch preflight check that verifies required runtime components and exact development versions"
    "  - scripts/launch_simulator_ui.ps1 = single-host manual-launch helper that verifies installation, starts the backend process, waits for /health, and shows a browser-open confirmation message box"
    "  - scripts/run_simulator.bat = Windows batch wrapper that opens a temporary PowerShell session for scripts/launch_simulator_ui.ps1 and closes it after the prompt flow completes"
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
    "11. Installation and Rapair Design"
    "Purpose: this chapter defines how future installer and repair flows shall create, verify, and restore the simulator runtime environment so local setups remain aligned with the development baseline."
    "Best-Practice Installation Rules:"
    "  - treat the repository .venv as disposable and reproducible rather than as a permanent hand-maintained asset"
    "  - create the virtual environment from the intended Python version explicitly instead of relying on whatever python happens to be on PATH"
    "  - install packages through the repository-local interpreter or its pip entry point so installation always targets the correct .venv"
    "  - pin exact dependency versions with == for repeatable installs"
    "  - prefer stronger reproducibility with hashes for tightly controlled or release-oriented installer flows"
    "  - treat the .venv as non-portable and recreate it when the environment is moved, damaged, or version-drifted"
    "  - run verification after install instead of assuming installation success implies runtime correctness"
    "Development Alignment Rules:"
    "  - the installer shall compare the local runtime against the documented Runtime Version Baseline"
    "  - alignment checks shall include the Python version and the exact package versions required by the simulator"
    "  - the launcher or installer shall fail fast when versions drift or required components are missing"
    "  - any failure report shall list concrete gaps so the operator knows exactly what is missing or mismatched"
    "Verification Scope for Installer and Repair:"
    "  - verify the repository-local Python interpreter exists"
    "  - verify the Python version matches the development baseline exactly"
    "  - verify each required runtime package is installed"
    "  - verify each required runtime package version matches the development baseline exactly"
    "  - verify the environment is not carrying stale or partially uninstalled distribution artifacts"
    "  - run package-health validation after installation or repair"
    "Preferred Repair Philosophy:"
    "  - a damaged or drifted .venv should normally be rebuilt, not patched manually"
    "  - the safest repair path is remove -> recreate -> reinstall -> reverify"
    "  - surgical package repair may be used only for clearly isolated minor issues, but full rebuild remains the default reliable method"
    "Repair Flow Design:"
    "  1. detect a missing component, version mismatch, or broken package artifact"
    "  2. present the gap list clearly to the operator"
    "  3. offer a Repair action instead of requiring manual reconstruction"
    "  4. delete the repository .venv"
    "  5. recreate the .venv using the required Python version"
    "  6. reinstall the pinned baseline dependencies"
    "  7. rerun verification"
    "  8. report success or failure with a clear popup summary"
    "Installer Integration Expectation:"
    "  - when a future installer is implemented, it shall read the version-baseline and verification requirements from this chapter and use them as the source of truth for setup and repair behavior"
    "Why This Design Is Preferred:"
    "  - rebuilding the environment is usually more reliable than trying to patch a partially corrupted venv"
    "  - explicit verification makes local runtime drift visible before the simulator launch fails later"
    "  - one-step repair keeps the workflow practical for operator test stations and development machines"
    ""
    "12. Simulator UI State and Command Feedback"
    "Command Button Rule: simulator command buttons use blue as the default unpressed color."
    "Pressed/In-Progress Rule: when a command button is pressed it turns gray and looks pressed while the related command or state transition is still in progress."
    "Completion Rule: after the related state/action finishes, the command button returns to the default blue unpressed state."
    "Server State Color Rules:"
    "  dark blue = inactive / default after reset"
    "  blinking green = in progress"
    "  red = finished with failure"
    "  light green = finished with success"
    "Grouping Rule: server-state indication shall be shown in a dedicated titled group box named Server States so state reporting remains visually separate from operator commands."
    "Editable Text Rule: all editable text controls in the simulator UI shall use a bright fill with a light visible frame so writable fields are visually distinct from static text boxes."
    "Logger Rule: the logger remains a separate rolling history surface and is not itself a machine-state indicator."
    ""
    "13. Notes"
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
