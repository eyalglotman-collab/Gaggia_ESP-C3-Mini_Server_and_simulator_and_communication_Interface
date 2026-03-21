<#
.SYNOPSIS
Generates the ESP32-C3 bridge firmware design description `.docx` artifact.

.DESCRIPTION
Builds the full bridge design description OpenXML package from section text and
the rendered diagram set under `docs\bridge_diagrams`, then writes the finished
`.docx` to `docs\bridge_design_description.docx`.

Run generate_bridge_design_diagrams.ps1 first to produce the required PNGs.
#>
[CmdletBinding()]
param()

$ProjectRoot      = Split-Path -Parent $PSScriptRoot
$TempDir          = Join-Path $ProjectRoot ".cache\bridge_design_docx_tmp"
$OutputDocx       = Join-Path $ProjectRoot "docs\bridge_design_description.docx"
$DiagramDir       = Join-Path $ProjectRoot "docs\bridge_diagrams"
$Version          = (Get-Content (Join-Path $ProjectRoot "VERSION") -Raw).Trim()
$FirmwareVersion  = (Get-Content (Join-Path $ProjectRoot "firmware\esp32c3_bridge\VERSION") -Raw).Trim()
$ReviewedOn       = Get-Date -Format 'dd-MMM-yy HH:mm:ss'
$MaxImageWidthEmu = [long](6.2 * 914400)

# ---------------------------------------------------------------------------
# Output path helper
# ---------------------------------------------------------------------------

# @brief Resolve a writable output path for the generated `.docx`.
# @details Falls back to a `.generated.docx` sibling when the file is locked.
# @param[in] PreferredPath Intended output path.
# @return Writable output path.
function Resolve-DocxOutputPath {
    param([Parameter(Mandatory=$true)][string]$PreferredPath)
    if (-not (Test-Path $PreferredPath)) { return $PreferredPath }
    try {
        $s = [System.IO.File]::Open($PreferredPath,[System.IO.FileMode]::Open,[System.IO.FileAccess]::ReadWrite,[System.IO.FileShare]::None)
        $s.Dispose()
        return $PreferredPath
    } catch {
        $dir  = Split-Path -Parent $PreferredPath
        $name = [System.IO.Path]::GetFileNameWithoutExtension($PreferredPath)
        $ext  = [System.IO.Path]::GetExtension($PreferredPath)
        return (Join-Path $dir ($name + ".generated" + $ext))
    }
}

# ---------------------------------------------------------------------------
# File helpers
# ---------------------------------------------------------------------------

# @brief Write a UTF-8 file without BOM.
function Write-Utf8File {
    param([Parameter(Mandatory=$true)][string]$Path,
          [Parameter(Mandatory=$true)][string]$Content)
    $enc = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($Path,$Content,$enc)
}

# ---------------------------------------------------------------------------
# OpenXML packaging
# ---------------------------------------------------------------------------

# @brief Create a docx ZIP package from named OpenXML parts.
function New-DocxPackage {
    param([Parameter(Mandatory=$true)][string]$OutputDocx,
          [Parameter(Mandatory=$true)][hashtable]$Parts)
    if (Test-Path $OutputDocx) { Remove-Item $OutputDocx -Force }
    Add-Type -AssemblyName System.IO.Compression
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $fs = [System.IO.File]::Open($OutputDocx,[System.IO.FileMode]::CreateNew)
    try {
        $zip = New-Object System.IO.Compression.ZipArchive($fs,[System.IO.Compression.ZipArchiveMode]::Create,$false)
        try {
            foreach ($name in $Parts.Keys) {
                $entry  = $zip.CreateEntry($name,[System.IO.Compression.CompressionLevel]::Optimal)
                $es     = $entry.Open()
                try {
                    $bytes = [System.IO.File]::ReadAllBytes($Parts[$name])
                    $es.Write($bytes,0,$bytes.Length)
                } finally { $es.Dispose() }
            }
        } finally { $zip.Dispose() }
    } finally { $fs.Dispose() }
}

# ---------------------------------------------------------------------------
# WordprocessingML fragment builders
# ---------------------------------------------------------------------------

# @brief Convert plain text to a paragraph XML fragment.
function ConvertTo-ParagraphXml {
    param([Parameter(Mandatory=$true)][AllowEmptyString()][string]$Text)
    if ([string]::IsNullOrEmpty($Text)) { return "<w:p/>" }
    $esc = [System.Security.SecurityElement]::Escape($Text)
    return "<w:p><w:r><w:t xml:space=`"preserve`">$esc</w:t></w:r></w:p>"
}

# @brief Convert text to a bold heading-style paragraph XML fragment.
function ConvertTo-HeadingXml {
    param([Parameter(Mandatory=$true)][string]$Text,[int]$Level=1)
    $esc  = [System.Security.SecurityElement]::Escape($Text)
    $size = switch ($Level) { 1 { 32 } 2 { 28 } default { 24 } }
    return "<w:p><w:pPr><w:spacing w:before=`"240`" w:after=`"60`"/></w:pPr><w:r><w:rPr><w:b/><w:sz w:val=`"$size`"/><w:szCs w:val=`"$size`"/></w:rPr><w:t xml:space=`"preserve`">$esc</w:t></w:r></w:p>"
}

# @brief Create a centered italic figure caption paragraph.
function New-CaptionParagraphXml {
    param([Parameter(Mandatory=$true)][string]$Caption)
    $esc = [System.Security.SecurityElement]::Escape($Caption)
    return "<w:p><w:pPr><w:jc w:val=`"center`"/><w:spacing w:before=`"60`" w:after=`"160`"/></w:pPr><w:r><w:rPr><w:i/><w:color w:val=`"8BBFD0`"/></w:rPr><w:t xml:space=`"preserve`">$esc</w:t></w:r></w:p>"
}

# @brief Create the drawing paragraph for one embedded PNG.
function New-DrawingParagraphXml {
    param([string]$RelationshipId,[string]$Name,[long]$WidthEmu,[long]$HeightEmu,[int]$DocPrId)
    $eName = [System.Security.SecurityElement]::Escape($Name)
    return @"
<w:p>
  <w:pPr><w:jc w:val="center"/><w:spacing w:before="120" w:after="60"/></w:pPr>
  <w:r>
    <w:drawing>
      <wp:inline distT="0" distB="0" distL="0" distR="0">
        <wp:extent cx="$WidthEmu" cy="$HeightEmu"/>
        <wp:effectExtent l="0" t="0" r="0" b="0"/>
        <wp:docPr id="$DocPrId" name="$eName"/>
        <wp:cNvGraphicFramePr>
          <a:graphicFrameLocks xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" noChangeAspect="1"/>
        </wp:cNvGraphicFramePr>
        <a:graphic xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
          <a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture">
            <pic:pic xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture">
              <pic:nvPicPr>
                <pic:cNvPr id="$DocPrId" name="$eName"/>
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

# ---------------------------------------------------------------------------
# Image metadata resolution
# ---------------------------------------------------------------------------

# @brief Build metadata map for all images referenced in body items.
function Get-ImageMap {
    param([string[]]$Markers)
    Add-Type -AssemblyName System.Drawing
    $map   = @{}
    $index = 1
    foreach ($marker in $Markers) {
        $parts    = $marker -split "\|",3
        $fileName = $parts[1]
        if ($map.ContainsKey($fileName)) { continue }
        $filePath = Join-Path $DiagramDir $fileName
        if (-not (Test-Path $filePath)) {
            Write-Warning "Diagram not found: $filePath - skipping"
            continue
        }
        $img = [System.Drawing.Image]::FromFile($filePath)
        try {
            $wEmu = [long]($img.Width  * 9525)
            $hEmu = [long]($img.Height * 9525)
            if ($wEmu -gt $MaxImageWidthEmu) {
                $scale = $MaxImageWidthEmu / $wEmu
                $wEmu  = [long]($wEmu * $scale)
                $hEmu  = [long]($hEmu * $scale)
            }
            $map[$fileName] = @{
                FileName       = $fileName
                FilePath       = $filePath
                RelationshipId = "rId$index"
                DocPrId        = $index + 100
                WidthEmu       = $wEmu
                HeightEmu      = $hEmu
                Target         = "media/$fileName"
            }
        } finally { $img.Dispose() }
        $index++
    }
    return $map
}

# ---------------------------------------------------------------------------
# Body XML assembly
# ---------------------------------------------------------------------------

# @brief Convert ordered body item list to WordprocessingML.
function ConvertTo-BodyXml {
    param([AllowEmptyString()][AllowEmptyCollection()][string[]]$Items,
          [hashtable]$ImageMap)
    $fragments = foreach ($item in $Items) {
        if ($item -match '^\[\[IMAGE\|([^|]+)\|(.+)\]\]$') {
            $fn   = $matches[1]
            $cap  = $matches[2]
            $info = $ImageMap[$fn]
            if ($null -eq $info) { ConvertTo-ParagraphXml -Text "[MISSING DIAGRAM: $fn]"; continue }
            New-CaptionParagraphXml -Caption $cap
            New-DrawingParagraphXml -RelationshipId $info.RelationshipId -Name $fn `
                -WidthEmu $info.WidthEmu -HeightEmu $info.HeightEmu -DocPrId $info.DocPrId
            "<w:p/>"
            continue
        }
        if ($item -match '^\[\[H1\|(.+)\]\]$') { ConvertTo-HeadingXml -Text $matches[1] -Level 1; continue }
        if ($item -match '^\[\[H2\|(.+)\]\]$') { ConvertTo-HeadingXml -Text $matches[1] -Level 2; continue }
        ConvertTo-ParagraphXml -Text $item
    }
    return ($fragments -join "`n")
}

# ===========================================================================
# Document content
# ===========================================================================
$bodyItems = @(
    "[[H1|Bridge Design Description - ESP32-C3 Serial Communication]]"
    "Document Status: Design Baseline"
    "Server Simulator Version: $Version"
    "Bridge Firmware Version:  $FirmwareVersion"
    "Generated: $ReviewedOn"
    "Owner: Eyal / Claude"
    ""
    # ------------------------------------------------------------------
    "[[H1|1. Purpose and Scope]]"
    "This document describes the complete design of the ESP32-C3-SuperMini bridge firmware that forms the low-level serial transport layer between the host Python simulator and the ESP32-S3 client application. It covers hardware topology, software architecture, communication protocol, state machines, frame format, memory allocation, error handling, build process, and operational workflow."
    ""
    "[[H2|1.1 System Role]]"
    "The ESP32-C3 bridge is a dedicated transport controller. It owns the USB serial link to the host PC and the Wi-Fi SoftAP TCP server link to the ESP32-S3 client. It does not implement application logic - it bridges, validates, and forwards framed messages between the two network segments."
    ""
    # ------------------------------------------------------------------
    "[[H1|2. Hardware Architecture]]"
    "[[H2|2.1 Physical Topology]]"
    "[[IMAGE|bridge_hw_topology.png|Figure 1. Hardware topology: Host PC via USB to ESP32-C3 bridge, Wi-Fi to ESP32-S3 client.]]"
    ""
    "[[H2|2.2 Hardware Components]]"
    "  ESP32-C3 SuperMini - RISC-V 160 MHz SoC, 4 MB flash, integrated USB PHY, 2.4 GHz Wi-Fi 802.11b/g/n."
    "  Host PC - Windows 10/11 workstation running the Python FastAPI simulator. Provides 5V power to the ESP32-C3 via USB."
    "  ESP32-S3 Client - Target application board. Connects over Wi-Fi as a TCP client to port 3333 on the bridge."
    ""
    "[[H2|2.3 Physical Interfaces]]"
    "  USB: Full-speed USB 2.0 CDC/JTAG. The ESP32-C3 enumerates as a USB serial device (COM port) visible to the host OS."
    "  Wi-Fi: The bridge creates a SoftAP with SSID EyalSimulatorAP, password espresso1234, channel 1, max 4 clients."
    "  TCP: The bridge runs a TCP server on port 3333. Only one client session is active at a time."
    ""
    # ------------------------------------------------------------------
    "[[H1|3. Software Architecture]]"
    "[[H2|3.1 Layer Stack]]"
    "[[IMAGE|bridge_sw_architecture.png|Figure 2. Software architecture layer stack from ESP-IDF hardware drivers to application entry point.]]"
    ""
    "[[H2|3.2 Source File Structure]]"
    "  bridge_main.c            - ESP-IDF app_main() entry; delegates directly to communication_functions_run()."
    "  CommunicationFunctions.c - All runtime logic: state machine, keepalive, frame encode/decode, USB/TCP I/O."
    "  CommunicationFunctions.h - Public API (single function) and shared compile-time constants."
    "  CMakeLists.txt           - ESP-IDF component build descriptor."
    "  sdkconfig.defaults       - Project-level Kconfig overrides (USB JTAG CDC enabled)."
    ""
    "[[H2|3.3 Threading Model]]"
    "The bridge runs as a single FreeRTOS task on the application CPU core. There are no additional tasks or ISR-based communication threads. All USB reads, TCP accept/recv/send, keepalive timing, and state transitions occur sequentially within the main loop, which polls every 20 ms via vTaskDelay()."
    ""
    "[[H2|3.4 Libraries and Dependencies]]"
    "  ESP-IDF v5.x - Framework providing FreeRTOS, Wi-Fi stack, TCP/IP stack (lwIP), USB JTAG CDC driver, NVS, and timer APIs."
    "  driver/usb_serial_jtag.h - USB serial I/O primitives: usb_serial_jtag_read_bytes(), usb_serial_jtag_write_bytes()."
    "  esp_wifi / esp_netif     - Wi-Fi SoftAP lifecycle: init, start, ap config, event loop."
    "  lwip/sockets.h           - BSD socket API: socket(), bind(), listen(), accept(), recv(), send(), setsockopt()."
    "  nvs_flash                - Non-volatile storage init required by Wi-Fi driver at startup."
    "  esp_timer                - esp_timer_get_time() for microsecond-resolution keepalive timestamps."
    "  freertos/task.h          - vTaskDelay() for 20 ms poll cadence."
    ""
    # ------------------------------------------------------------------
    "[[H1|4. Communication Protocol]]"
    "[[H2|4.1 Frame Format]]"
    "[[IMAGE|bridge_frame_format.png|Figure 3. Binary frame layout: SOF, message type, live integers, sequence, payload length, CRC16, and payload.]]"
    ""
    "[[H2|4.2 Frame Fields (all little-endian)]]"
    "  SOF[0]         1 B   Fixed 0xA5."
    "  SOF[1]         1 B   Fixed 0x5A."
    "  MsgType        1 B   Message type enum 1-8, see below."
    "  HostLiveInt    4 B   uint32. Server-side liveness counter (even numbers, incremented per keepalive)."
    "  DeviceLiveInt  4 B   uint32. Client-side liveness counter (odd numbers, incremented per keepalive reply)."
    "  Sequence       2 B   uint16. Per-transport rolling sequence number."
    "  PayloadLength  2 B   uint16. Number of payload bytes, 0 to BRIDGE_FRAME_MAX_PAYLOAD=600."
    "  CRC16          2 B   uint16. CRC16-CCITT over all fields except the CRC field itself."
    "  Payload        0-600 B  Variable-length payload data."
    ""
    "[[H2|4.3 Message Types]]"
    "  1 = RESET        Host instructs bridge to clear state and restart."
    "  2 = INITIALIZE   Host sends configuration; bridge prepares Wi-Fi/TCP resources."
    "  3 = CONNECT      Host requests client session start; bridge begins accepting TCP connections."
    "  4 = DISCONNECT   Either side requests controlled session teardown."
    "  5 = KEEPALIVE    Periodic liveness frame exchanged between host and client via bridge."
    "  6 = ERROR        Either side reports a transport fault."
    "  7 = ACK          Bridge acknowledges a received command from the host."
    "  8 = DATA         Binary application data forwarded bidirectionally."
    ""
    "[[H2|4.4 Text Payload Format (types 1-7)]]"
    "Text payloads are null-terminated ASCII key=value strings (max 256 bytes). Examples:"
    "  KEEPALIVE request:  session_id=1 request_id=5 live=10"
    "  KEEPALIVE reply:    session_id=1 request_id=5 rver=1 delay_ms=28 max_delay_ms=30 errors=0"
    "  INITIALIZE:         data_ver=1"
    ""
    "[[H2|4.5 Binary DATA Payload Format]]"
    "[[IMAGE|bridge_data_forwarding.png|Figure 4. Binary DATA channel: downlink from Python server to client, uplink from client to server.]]"
    ""
    "  Downlink (server -> bridge -> client):"
    "    payload[0]    = 0xD0 (magic)"
    "    payload[1..4] = seq uint32 LE"
    "    payload[5..N] = 100 floats + 20 int32s + 50-byte string  (535 bytes total)"
    ""
    "  Uplink (client -> bridge -> server):"
    "    payload[0]    = 0xD1 (magic)"
    "    payload[1..4] = seq uint32 LE"
    "    payload[5..8] = timestamp_ms uint32 LE"
    "    payload[9..N] = 100 floats + 20 int32s + 50-byte string  (539 bytes total)"
    ""
    # ------------------------------------------------------------------
    "[[H1|5. State Machine]]"
    "[[H2|5.1 Bridge State Machine Diagram]]"
    "[[IMAGE|bridge_state_machine.png|Figure 5. Bridge state machine: RESET -> INITIALIZE -> CONNECT -> KEEPALIVE cycle -> ERROR and DISCONNECT.]]"
    ""
    "[[H2|5.2 States]]"
    "  RESET (0)                  - Entry state after power-on or after any fault recovery. Awaits RESET message from host."
    "  INITIALIZE (1)             - Bridge configures Wi-Fi SoftAP and TCP listener. Awaits CONNECT message."
    "  CONNECT (2)                - Bridge enters TCP accept loop awaiting a client connection. Once accepted, begins keepalive."
    "  KEEPALIVE_SERVER_SEND (3)  - Bridge sends a KEEPALIVE frame over TCP, records timestamp, starts 300 ms window."
    "  KEEPALIVE_CLIENT_RETURN (4)- Bridge awaits the client's KEEPALIVE reply within a 450 ms window."
    "  DISCONNECT (5)             - Controlled teardown: TCP client closed, ACK sent to host, transitions to RESET."
    "  ERROR (6)                  - Latched fault state. Fault reason sent to USB host. Awaits RESET or INITIALIZE from host."
    ""
    "[[H2|5.3 State Transition Triggers]]"
    "  RESET -> INITIALIZE:              Host sends RESET frame; bridge ACKs and advances."
    "  INITIALIZE -> CONNECT:            Host sends INITIALIZE with data_ver=1; bridge validates, starts Wi-Fi, ACKs."
    "  CONNECT -> KEEPALIVE_SERVER_SEND: TCP client accepted on port 3333."
    "  KEEPALIVE_SERVER_SEND -> KEEPALIVE_CLIENT_RETURN: KEEPALIVE frame sent over TCP."
    "  KEEPALIVE_CLIENT_RETURN -> KEEPALIVE_SERVER_SEND: Valid reply received within window; cycle repeats."
    "  Any active state -> DISCONNECT:   Host sends DISCONNECT frame."
    "  Any active state -> ERROR:         CRC mismatch, keepalive timeout (after 3 retries), TCP socket error, or running integer stall."
    "  ERROR -> RESET:                   Host sends RESET frame."
    "  ERROR -> INITIALIZE:              Host sends INITIALIZE frame (soft recovery, keeps Wi-Fi stack up)."
    ""
    # ------------------------------------------------------------------
    "[[H1|6. Main Loop Architecture]]"
    "[[H2|6.1 Main Loop Flowchart]]"
    "[[IMAGE|bridge_main_loop.png|Figure 6. Main loop sequence: init, USB poll, frame decode, keepalive service, 20 ms yield.]]"
    ""
    "[[H2|6.2 Poll Cycle Description]]"
    "  1. USB JTAG read - usb_serial_jtag_read_bytes() with 0 ms timeout. Bytes appended to assembly buffer."
    "  2. Frame parser  - Scans buffer for SOF 0xA5 0x5A, extracts length, validates CRC16, dispatches complete frames."
    "  3. Frame handler - Dispatches by message type. RESET/INITIALIZE/CONNECT/DISCONNECT/KEEPALIVE/ERROR/DATA each invoke dedicated handler."
    "  4. Keepalive engine - Checks elapsed time since last keepalive TX. If >= 300 ms, enters KEEPALIVE_SERVER_SEND. Checks reply window timeout. Manages retry count (max 3)."
    "  5. Transport watchdog - Checks that server HostLiveInteger has advanced in the expected window. If stalled: ERROR."
    "  6. vTaskDelay(20 ms) - Yields CPU, preventing busy-loop. Allows FreeRTOS scheduler to run background Wi-Fi tasks."
    ""
    # ------------------------------------------------------------------
    "[[H1|7. Keepalive Protocol]]"
    "[[H2|7.1 Keepalive Sequence]]"
    "[[IMAGE|bridge_keepalive_sequence.png|Figure 7. Keepalive three-party sequence: Python server, ESP32-C3 bridge, ESP32-S3 client.]]"
    ""
    "[[H2|7.2 Keepalive Timing Constants]]"
    "  BRIDGE_KEEPALIVE_PERIOD_MS      = 300 ms  - Interval between consecutive keepalive transmissions."
    "  BRIDGE_KEEPALIVE_WAIT_WINDOW_MS = 450 ms  - Maximum wait for client reply per keepalive."
    "  BRIDGE_RUNNING_INTEGER_RETRY_LIMIT = 3    - Max consecutive timeouts before ERROR transition."
    ""
    "[[H2|7.3 Live Integer Protocol]]"
    "  ServerLiveInteger: even numbers (0, 2, 4...). Incremented by the Python server each keepalive cycle."
    "  DeviceLiveInteger: odd numbers (1, 3, 5...). Incremented by the ESP32-S3 client in keepalive reply."
    "  The bridge validates that both integers advance correctly. Stalled integers trigger the running integer failure path."
    "  Telemetry: delay_ms (round-trip of last keepalive), max_delay_ms (maximum ever seen), total errors."
    ""
    # ------------------------------------------------------------------
    "[[H1|8. Memory Allocation]]"
    "[[H2|8.1 Memory Layout]]"
    "[[IMAGE|bridge_memory_layout.png|Figure 8. Key static memory allocations: USB buffers, TCP buffer, frame struct, telemetry counters, and ESP-IDF managed heap.]]"
    ""
    "[[H2|8.2 Static Allocations (compile-time)]]"
    "  BRIDGE_RX_BUFFER_SIZE     = 1024 B  - USB assembly scratch buffer (stack)."
    "  BRIDGE_TX_BUFFER_SIZE     = 1024 B  - USB transmit staging buffer (stack)."
    "  BRIDGE_TCP_RX_BUFFER_SIZE = 1280 B  - TCP receive buffer (static global)."
    "  BRIDGE_FRAME_MAX_PAYLOAD  = 600  B  - Maximum frame payload size."
    "  BRIDGE_FRAME_MAX_SIZE     = 617  B  - Includes 17-byte frame overhead."
    "  bridge_frame_t            = ~614 B  - Decoded frame struct (static global)."
    ""
    "[[H2|8.3 Dynamic Allocations (ESP-IDF heap)]]"
    "  Wi-Fi driver internal buffers - allocated by ESP-IDF Wi-Fi subsystem."
    "  lwIP pbuf pool              - TCP/IP receive and transmit packet buffers managed by lwIP."
    "  netif descriptor            - allocated by esp_netif."
    "  No heap allocation occurs in the bridge hot path - all frame processing uses stack or static memory."
    ""
    "[[H2|8.4 Flash Footprint]]"
    "  Bridge firmware binary: approximately 250 to 300 KB including ESP-IDF Wi-Fi stack."
    "  NVS partition: 16 KB for Wi-Fi calibration data."
    "  Total flash: 4 MB device - firmware occupies less than 10%."
    ""
    # ------------------------------------------------------------------
    "[[H1|9. Error Handling]]"
    "[[H2|9.1 Error Recovery Diagram]]"
    "[[IMAGE|bridge_error_recovery.png|Figure 9. Error sources (CRC, keepalive timeout, running integer stall, TCP error) and recovery paths (RESET, INITIALIZE, DISCONNECT).]]"
    ""
    "[[H2|9.2 Error Categories]]"
    "  CRC Mismatch:           Frame integrity failure. Frame is dropped silently. Error count incremented."
    "  Keepalive Timeout:      Client did not reply within 450 ms. Retry up to 3 times. After 3 failures -> ERROR."
    "  Running Integer Stall:  HostLiveInteger from server has not advanced. Assume PC host stuck -> ERROR."
    "  TCP Socket Error:       accept/recv/send returned error. TCP client closed, re-enter CONNECT listen loop."
    "  USB Read Error:         usb_serial_jtag_read_bytes returned error or 0. Logged, poll continues."
    ""
    "[[H2|9.3 SO_SNDTIMEO Protection]]"
    "A 50 ms SO_SNDTIMEO send timeout is set on the TCP client socket immediately after accept(). This prevents a blocked send() call from stalling the single bridge task when the TCP receive window closes. Without this protection, a slow or unresponsive TCP client could overflow the USB JTAG FIFO and cause byte loss on the host serial link."
    ""
    "[[H2|9.4 Recovery Actions]]"
    "  RESET frame from host:       Full bridge reset. Clears all state, counters, and Wi-Fi transport."
    "  INITIALIZE frame from host:  Soft re-entry. Re-prepares resources from INITIALIZE state (keeps Wi-Fi stack)."
    "  DISCONNECT frame from host:  Graceful shutdown. Closes TCP client, ACKs host, returns to RESET."
    ""
    # ------------------------------------------------------------------
    "[[H1|10. Build and Compilation]]"
    "[[H2|10.1 Toolchain]]"
    "  Framework:  ESP-IDF v5.x."
    "  Compiler:   xtensa-esp-elf-gcc / riscv32-esp-elf-gcc (ESP32-C3 uses RISC-V toolchain)."
    "  Build tool: idf.py (CMake + Ninja backend)."
    "  Host shell: Windows cmd.exe with ESP-IDF environment activated via export.bat / setup_idf_env.ps1."
    ""
    "[[H2|10.2 Build Commands (Mandatory Method)]]"
    "  Build:"
    "    cmd.exe /c C:\Espressif\Eyal_Projects_ESP32_S3\Eyal_espresso_server_simulator\scripts\idfw.cmd build"
    ""
    "  Flash (replace COM4 with actual port):"
    "    cmd.exe /c C:\Espressif\Eyal_Projects_ESP32_S3\Eyal_espresso_server_simulator\scripts\idfw.cmd -p COM4 flash"
    ""
    "  Build then flash (sequential):"
    "    cmd.exe /c ... idfw.cmd build && cmd.exe /c ... idfw.cmd -p COM4 flash"
    ""
    "[[H2|10.3 Important Build Notes]]"
    "  The MSYSTEM environment variable must be cleared before invoking idfw.cmd to prevent MinGW/MSYS detection:"
    "    set MSYSTEM= (cleared in idfw.cmd before calling idf.py)"
    "  COM port must be released by the Python simulator before flashing:"
    "    POST http://localhost:8000/api/transport/release-com (called by idfw.ps1 wrapper)"
    "  The idfw.ps1 and flash_hidden.ps1 scripts call the simulator release endpoint automatically."
    ""
    "[[H2|10.4 CMakeLists.txt Structure]]"
    "  cmake_minimum_required(VERSION 3.16)"
    "  include(\$ENV{IDF_PATH}/tools/cmake/project.cmake)"
    "  project(esp32c3_bridge)"
    "  Components: main (bridge_main.c, CommunicationFunctions.c, CommunicationFunctions.h)"
    "  Requires:  driver, esp_wifi, esp_netif, esp_event, nvs_flash, lwip, esp_timer"
    ""
    "[[H2|10.5 sdkconfig.defaults Key Settings]]"
    "  CONFIG_ESP_CONSOLE_USB_SERIAL_JTAG=y  - Enable USB JTAG CDC console."
    "  CONFIG_FREERTOS_HZ=1000              - 1 ms FreeRTOS tick for accurate keepalive timing."
    "  CONFIG_ESP32C3_REV_MIN_FULL=3        - Minimum chip revision."
    ""
    # ------------------------------------------------------------------
    "[[H1|11. Operational Workflow]]"
    "[[H2|11.1 Normal Startup Sequence]]"
    "  1. Power ESP32-C3 via USB from host PC."
    "  2. Bridge firmware boots: nvs_flash init -> Wi-Fi stack init -> USB JTAG driver install -> TCP socket created."
    "  3. Launch Python simulator on host PC."
    "  4. Operator selects COM port (e.g. COM4) in simulator UI and clicks Open Transport."
    "  5. Simulator sends RESET frame over USB. Bridge ACKs, enters INITIALIZE state."
    "  6. Simulator sends INITIALIZE frame (data_ver=1). Bridge starts SoftAP, starts TCP listener, ACKs."
    "  7. Simulator sends CONNECT frame. Bridge waits for TCP client."
    "  8. ESP32-S3 joins EyalSimulatorAP Wi-Fi and connects TCP to 192.168.4.1:3333."
    "  9. Bridge enters KEEPALIVE_SERVER_SEND. Normal keepalive cycle begins (300 ms period)."
    " 10. DATA frames flow bidirectionally: Python data_payload.py generates downlink at 2 Hz; client produces uplink."
    ""
    "[[H2|11.2 Fault Recovery Workflow]]"
    "  1. On any fault: bridge transitions to ERROR, sends ERROR frame to USB host with reason string."
    "  2. Simulator UI shows fault reason and error count."
    "  3. Operator clicks Reset or Initialize in simulator UI."
    "  4. Simulator sends RESET or INITIALIZE frame."
    "  5. Bridge clears fault state and re-enters the command sequence."
    ""
    "[[H2|11.3 Monitoring]]"
    "  USB console output: Tagged with [bridge] via ESP_LOGx. Visible in idf.py monitor (COM4)."
    "  Simulator UI telemetry: Transport Last Delay [mS], Transport Max Delay [mS], Total Errors - updated each keepalive."
    "  sim_debug.log: Optional log file capture via run_sim_debug.ps1 for session-level tracing."
    ""
    # ------------------------------------------------------------------
    "[[H1|12. Version Control]]"
    "  Bridge firmware version is tracked in firmware/esp32c3_bridge/VERSION."
    "  Server simulator version is tracked in VERSION."
    "  Both follow X.Y.Z format: X=major feature changes, Y=bug fixes, Z=verified patch increments."
    "  Versioning rules: increment Z on every accepted verification cycle; increment Y on bug fixes; X on architectural changes."
    "  Current versions at document generation:  Server=$Version  Bridge=$FirmwareVersion"
    ""
)

# ===========================================================================
# Package assembly
# ===========================================================================

if (-not (Test-Path $DiagramDir)) {
    throw "Diagram directory '$DiagramDir' not found. Run generate_bridge_design_diagrams.ps1 first."
}

if (Test-Path $TempDir) { Remove-Item $TempDir -Recurse -Force }
New-Item -ItemType Directory -Force $TempDir             | Out-Null
New-Item -ItemType Directory -Force (Join-Path $TempDir "_rels")        | Out-Null
New-Item -ItemType Directory -Force (Join-Path $TempDir "word")         | Out-Null
New-Item -ItemType Directory -Force (Join-Path $TempDir "word\_rels")   | Out-Null
New-Item -ItemType Directory -Force (Join-Path $TempDir "word\media")   | Out-Null

# Collect image markers
$imageMarkers = $bodyItems | Where-Object { $_ -match '^\[\[IMAGE\|' }
$imageMap     = Get-ImageMap -Markers $imageMarkers

# Build document body XML
$bodyXml = ConvertTo-BodyXml -Items $bodyItems -ImageMap $imageMap

# Build image relationships XML
$imgRelParts = $imageMap.Values | ForEach-Object {
    $target = "media/$($_.FileName)"
    "<Relationship Id=`"$($_.RelationshipId)`" Type=`"http://schemas.openxmlformats.org/officeDocument/2006/relationships/image`" Target=`"$target`"/>"
}
$imgRelXml = $imgRelParts -join "`n"

# Copy image files into media folder
foreach ($info in $imageMap.Values) {
    Copy-Item $info.FilePath (Join-Path $TempDir "word\media\$($info.FileName)") -Force
}

# [Content_Types].xml
$contentTypesXml = @"
<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml"  ContentType="application/xml"/>
  <Default Extension="png"  ContentType="image/png"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>
"@

# _rels/.rels
$rootRelsXml = @"
<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>
"@

# word/_rels/document.xml.rels
$docRelsXml = @"
<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
$imgRelXml
</Relationships>
"@

# word/document.xml
$documentXml = @"
<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document
  xmlns:wpc="http://schemas.microsoft.com/office/word/2010/wordprocessingCanvas"
  xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006"
  xmlns:o="urn:schemas-microsoft-com:office:office"
  xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"
  xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math"
  xmlns:v="urn:schemas-microsoft-com:vml"
  xmlns:wp14="http://schemas.microsoft.com/office/word/2010/wordprocessingDrawing"
  xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
  xmlns:w10="urn:schemas-microsoft-com:office:word"
  xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"
  xmlns:w14="http://schemas.microsoft.com/office/word/2010/wordml"
  xmlns:wpg="http://schemas.microsoft.com/office/word/2010/wordprocessingGroup"
  xmlns:wpi="http://schemas.microsoft.com/office/word/2010/wordprocessingInk"
  xmlns:wne="http://schemas.microsoft.com/office/word/2006/wordml"
  xmlns:wps="http://schemas.microsoft.com/office/word/2010/wordprocessingShape"
  mc:Ignorable="w14 wp14">
  <w:body>
    <w:sectPr>
      <w:pgSz w:w="12240" w:h="15840"/>
      <w:pgMar w:top="1080" w:right="1080" w:bottom="1080" w:left="1080"/>
    </w:sectPr>
    $bodyXml
  </w:body>
</w:document>
"@

# Write temp files
Write-Utf8File -Path (Join-Path $TempDir "[Content_Types].xml")       -Content $contentTypesXml
Write-Utf8File -Path (Join-Path $TempDir "_rels\.rels")               -Content $rootRelsXml
Write-Utf8File -Path (Join-Path $TempDir "word\_rels\document.xml.rels") -Content $docRelsXml
Write-Utf8File -Path (Join-Path $TempDir "word\document.xml")         -Content $documentXml

# Assemble final package
$parts = @{
    "[Content_Types].xml"        = (Join-Path $TempDir "[Content_Types].xml")
    "_rels/.rels"                = (Join-Path $TempDir "_rels\.rels")
    "word/document.xml"          = (Join-Path $TempDir "word\document.xml")
    "word/_rels/document.xml.rels" = (Join-Path $TempDir "word\_rels\document.xml.rels")
}
foreach ($info in $imageMap.Values) {
    $parts["word/media/$($info.FileName)"] = (Join-Path $TempDir "word\media\$($info.FileName)")
}

$outputPath = Resolve-DocxOutputPath -PreferredPath $OutputDocx
New-DocxPackage -OutputDocx $outputPath -Parts $parts

# Clean up temp directory
Remove-Item $TempDir -Recurse -Force

Write-Host ""
Write-Host "Bridge design description generated: $outputPath"
$item = Get-Item $outputPath
Write-Host "  Size: $([math]::Round($item.Length/1024,1)) KB"
Write-Host "  Diagrams embedded: $($imageMap.Count)"
