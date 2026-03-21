<#
.SYNOPSIS
Renders the ESP32-C3 bridge firmware design description diagrams.

.DESCRIPTION
Uses `System.Drawing` to generate PNG diagrams covering the bridge hardware
topology, software architecture, state machine, frame format, main loop
flowchart, keepalive sequence, data forwarding, memory layout, and error
recovery. The generated PNGs are saved to docs\bridge_diagrams and are later
embedded by generate_bridge_design_docx.ps1.
#>
[CmdletBinding()]
param()

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$OutputDir   = Join-Path $ProjectRoot "docs\bridge_diagrams"

Add-Type -AssemblyName System.Drawing

# ---------------------------------------------------------------------------
# Shared canvas helpers (same style as generate_transport_diagrams.ps1)
# ---------------------------------------------------------------------------

# @brief Create a rounded rectangle graphics path.
# @param[in] X Left coordinate.
# @param[in] Y Top coordinate.
# @param[in] Width Rectangle width.
# @param[in] Height Rectangle height.
# @param[in] Radius Corner radius.
# @return GraphicsPath for the rounded rectangle.
function New-RoundedRectanglePath {
    param([float]$X,[float]$Y,[float]$Width,[float]$Height,[float]$Radius)
    $d    = $Radius * 2
    $path = New-Object System.Drawing.Drawing2D.GraphicsPath
    $path.AddArc($X,              $Y,               $d, $d, 180, 90)
    $path.AddArc($X+$Width-$d,   $Y,               $d, $d, 270, 90)
    $path.AddArc($X+$Width-$d,   $Y+$Height-$d,    $d, $d,   0, 90)
    $path.AddArc($X,              $Y+$Height-$d,    $d, $d,  90, 90)
    $path.CloseFigure()
    return $path
}

# @brief Create a new diagram canvas with dark background.
# @param[in] Width Bitmap width.
# @param[in] Height Bitmap height.
# @return Hashtable with Bitmap, Graphics, Width, Height.
function New-Canvas {
    param([int]$Width=1400,[int]$Height=800)
    $bmp = New-Object System.Drawing.Bitmap $Width,$Height
    $g   = [System.Drawing.Graphics]::FromImage($bmp)
    $g.SmoothingMode        = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
    $g.InterpolationMode    = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
    $g.PixelOffsetMode      = [System.Drawing.Drawing2D.PixelOffsetMode]::HighQuality
    $g.TextRenderingHint    = [System.Drawing.Text.TextRenderingHint]::ClearTypeGridFit
    $g.Clear([System.Drawing.Color]::FromArgb(8,16,28))
    return @{ Bitmap=$bmp; Graphics=$g; Width=$Width; Height=$Height }
}

# @brief Save and dispose a canvas.
function Save-Canvas {
    param([hashtable]$Canvas,[string]$Path)
    $Canvas.Bitmap.Save($Path,[System.Drawing.Imaging.ImageFormat]::Png)
    $Canvas.Graphics.Dispose()
    $Canvas.Bitmap.Dispose()
}

# @brief Draw diagram title at top-left.
function Draw-Title {
    param([System.Drawing.Graphics]$Graphics,[string]$Text)
    $font  = New-Object System.Drawing.Font("Arial",22,[System.Drawing.FontStyle]::Bold)
    $brush = New-Object System.Drawing.SolidBrush([System.Drawing.Color]::FromArgb(232,247,255))
    try { $Graphics.DrawString($Text,$font,$brush,40,22) }
    finally { $brush.Dispose(); $font.Dispose() }
}

# @brief Draw a labeled rounded box.
function Draw-Box {
    param(
        [System.Drawing.Graphics]$Graphics,
        [float]$X,[float]$Y,[float]$Width,[float]$Height,
        [string]$Title,[string]$Body,
        [System.Drawing.Color]$FillColor,
        [System.Drawing.Color]$LineColor
    )
    $path       = New-RoundedRectanglePath -X $X -Y $Y -Width $Width -Height $Height -Radius 20
    $fillBrush  = New-Object System.Drawing.SolidBrush($FillColor)
    $linePen    = New-Object System.Drawing.Pen($LineColor,3)
    $titleFont  = New-Object System.Drawing.Font("Arial",15,[System.Drawing.FontStyle]::Bold)
    $bodyFont   = New-Object System.Drawing.Font("Consolas",11,[System.Drawing.FontStyle]::Regular)
    $titleBrush = New-Object System.Drawing.SolidBrush([System.Drawing.Color]::FromArgb(236,250,255))
    $bodyBrush  = New-Object System.Drawing.SolidBrush([System.Drawing.Color]::FromArgb(196,229,240))
    $bodyRect   = New-Object System.Drawing.RectangleF ($X+16),($Y+44),($Width-32),($Height-56)
    try {
        $Graphics.FillPath($fillBrush,$path)
        $Graphics.DrawPath($linePen,$path)
        $Graphics.DrawString($Title,$titleFont,$titleBrush,$X+16,$Y+12)
        $Graphics.DrawString($Body,$bodyFont,$bodyBrush,$bodyRect)
    } finally {
        $bodyBrush.Dispose(); $titleBrush.Dispose(); $bodyFont.Dispose()
        $titleFont.Dispose(); $linePen.Dispose(); $fillBrush.Dispose(); $path.Dispose()
    }
}

# @brief Draw a cyan directional arrow with optional label.
function Draw-Arrow {
    param(
        [System.Drawing.Graphics]$Graphics,
        [float]$X1,[float]$Y1,[float]$X2,[float]$Y2,
        [string]$Label=""
    )
    $pen = New-Object System.Drawing.Pen([System.Drawing.Color]::FromArgb(94,228,255),4)
    $cap = New-Object System.Drawing.Drawing2D.AdjustableArrowCap(6,8,$true)
    $pen.CustomEndCap = $cap
    try {
        $Graphics.DrawLine($pen,$X1,$Y1,$X2,$Y2)
        if (-not [string]::IsNullOrWhiteSpace($Label)) {
            $font  = New-Object System.Drawing.Font("Arial",11,[System.Drawing.FontStyle]::Bold)
            $brush = New-Object System.Drawing.SolidBrush([System.Drawing.Color]::FromArgb(172,235,255))
            try {
                $mx = (($X1+$X2)/2)-44
                $my = (($Y1+$Y2)/2)-22
                $Graphics.DrawString($Label,$font,$brush,$mx,$my)
            } finally { $brush.Dispose(); $font.Dispose() }
        }
    } finally { $cap.Dispose(); $pen.Dispose() }
}

# @brief Draw a small label (no background).
function Draw-Label {
    param([System.Drawing.Graphics]$Graphics,[string]$Text,[float]$X,[float]$Y,[int]$Size=11,[System.Drawing.Color]$Color)
    if (-not $PSBoundParameters.ContainsKey('Color')) {
        $Color = [System.Drawing.Color]::FromArgb(200,235,255)
    }
    $font  = New-Object System.Drawing.Font("Arial",$Size,[System.Drawing.FontStyle]::Regular)
    $brush = New-Object System.Drawing.SolidBrush($Color)
    try { $Graphics.DrawString($Text,$font,$brush,$X,$Y) }
    finally { $brush.Dispose(); $font.Dispose() }
}

# ---------------------------------------------------------------------------
# Color palette helpers
# ---------------------------------------------------------------------------
function Get-BoxColors {
    param([string]$Kind)
    switch ($Kind) {
        "hw"       { return @{ F=[System.Drawing.Color]::FromArgb(16,38,62); L=[System.Drawing.Color]::FromArgb(91,200,255) } }
        "fw"       { return @{ F=[System.Drawing.Color]::FromArgb(18,54,74); L=[System.Drawing.Color]::FromArgb(87,245,255) } }
        "python"   { return @{ F=[System.Drawing.Color]::FromArgb(16,44,70); L=[System.Drawing.Color]::FromArgb(91,230,255) } }
        "state"    { return @{ F=[System.Drawing.Color]::FromArgb(16,44,70); L=[System.Drawing.Color]::FromArgb(91,230,255) } }
        "error"    { return @{ F=[System.Drawing.Color]::FromArgb(66,24,36); L=[System.Drawing.Color]::FromArgb(255,122,151) } }
        "warn"     { return @{ F=[System.Drawing.Color]::FromArgb(44,42,20); L=[System.Drawing.Color]::FromArgb(255,196,98)  } }
        "data"     { return @{ F=[System.Drawing.Color]::FromArgb(14,48,38); L=[System.Drawing.Color]::FromArgb(80,230,160)  } }
        default    { return @{ F=[System.Drawing.Color]::FromArgb(20,36,54); L=[System.Drawing.Color]::FromArgb(120,200,255) } }
    }
}

# ===========================================================================
# Diagram 1 - Hardware Topology
# ===========================================================================
function Draw-HWTopology {
    param([string]$Path)
    $canvas = New-Canvas -Width 1700 -Height 780
    $g = $canvas.Graphics
    Draw-Title -Graphics $g -Text "Bridge Hardware Topology"

    $hw = Get-BoxColors "hw"
    $fw = Get-BoxColors "fw"
    $py = Get-BoxColors "python"

    # PC
    Draw-Box -Graphics $g -X 40 -Y 200 -Width 300 -Height 220 `
        -Title "Host PC" `
        -Body "Windows 10/11`nPython / FastAPI server`nSimulator UI (browser)`nCOM port owner`nUSB-CDC driver" `
        -FillColor $py.F -LineColor $py.L

    # USB cable
    Draw-Arrow -Graphics $g -X1 340 -Y1 310 -X2 470 -Y2 310 -Label "USB cable"

    # ESP32-C3 SuperMini
    Draw-Box -Graphics $g -X 470 -Y 140 -Width 360 -Height 400 `
        -Title "ESP32-C3 SuperMini" `
        -Body "USB JTAG/Serial CDC`nFreeRTOS single task`nWi-Fi SoftAP driver`nlwIP TCP/IP stack`nTCP server port 3333`nFrame codec (CRC16)`nState machine" `
        -FillColor $fw.F -LineColor $fw.L

    # Wi-Fi RF
    Draw-Arrow -Graphics $g -X1 830 -Y1 310 -X2 960 -Y2 310 -Label "Wi-Fi 2.4G"

    # ESP32-S3
    Draw-Box -Graphics $g -X 960 -Y 180 -Width 360 -Height 340 `
        -Title "ESP32-S3 Client" `
        -Body "Wi-Fi station mode`nTCP client port 3333`nTransport state machine`nLVGL UI`nApplication logic" `
        -FillColor $hw.F -LineColor $hw.L

    # Power note
    Draw-Box -Graphics $g -X 470 -Y 580 -Width 360 -Height 100 `
        -Title "Power" `
        -Body "5V via USB from host PC" `
        -FillColor ([System.Drawing.Color]::FromArgb(20,32,44)) `
        -LineColor ([System.Drawing.Color]::FromArgb(100,160,200))

    Draw-Label -Graphics $g -Text "SSID: EyalSimulatorAP   /   Password: espresso1234   /   TCP port: 3333" `
               -X 200 -Y 700 -Size 13

    Save-Canvas -Canvas $canvas -Path $Path
}

# ===========================================================================
# Diagram 2 - Software Architecture Layers
# ===========================================================================
function Draw-SWArchitecture {
    param([string]$Path)
    $canvas = New-Canvas -Width 1600 -Height 820
    $g = $canvas.Graphics
    Draw-Title -Graphics $g -Text "Software Architecture - Layer Stack"

    $fw = Get-BoxColors "fw"
    $hw = Get-BoxColors "hw"

    Draw-Box -Graphics $g -X 80 -Y 100 -Width 1440 -Height 90 `
        -Title "ESP-IDF Application Layer" `
        -Body "bridge_main.c: app_main delegates to communication_functions_run" `
        -FillColor $fw.F -LineColor $fw.L
    Draw-Arrow -Graphics $g -X1 800 -Y1 190 -X2 800 -Y2 196

    Draw-Box -Graphics $g -X 80 -Y 210 -Width 1440 -Height 110 `
        -Title "Communication Logic Layer" `
        -Body "CommunicationFunctions.c: state machine, keepalive engine,`nwatchdog, telemetry, frame dispatch" `
        -FillColor $fw.F -LineColor $fw.L
    Draw-Arrow -Graphics $g -X1 800 -Y1 320 -X2 800 -Y2 326

    Draw-Box -Graphics $g -X 80 -Y 340 -Width 1440 -Height 90 `
        -Title "Frame Codec Layer" `
        -Body "SOF=0xA5 0x5A, CRC16-CCITT, header 15 B, payload up to 600 B" `
        -FillColor $fw.F -LineColor $fw.L
    Draw-Arrow -Graphics $g -X1 800 -Y1 430 -X2 800 -Y2 436

    Draw-Box -Graphics $g -X 80 -Y 450 -Width 1440 -Height 90 `
        -Title "Transport Abstraction Layer" `
        -Body "USB JTAG CDC (usb_serial_jtag driver)   /   lwIP BSD sockets TCP" `
        -FillColor $fw.F -LineColor $fw.L
    Draw-Arrow -Graphics $g -X1 800 -Y1 540 -X2 800 -Y2 546

    Draw-Box -Graphics $g -X 80 -Y 560 -Width 1440 -Height 90 `
        -Title "ESP-IDF OS and Drivers" `
        -Body "FreeRTOS   esp_wifi   esp_netif   nvs_flash   esp_timer" `
        -FillColor $hw.F -LineColor $hw.L
    Draw-Arrow -Graphics $g -X1 800 -Y1 650 -X2 800 -Y2 656

    Draw-Box -Graphics $g -X 80 -Y 670 -Width 1440 -Height 80 `
        -Title "Hardware" `
        -Body "ESP32-C3 SoC   USB PHY   Wi-Fi 802.11b/g/n   4 MB Flash" `
        -FillColor $hw.F -LineColor $hw.L

    Save-Canvas -Canvas $canvas -Path $Path
}

# ===========================================================================
# Diagram 3 - Bridge State Machine
# ===========================================================================
function Draw-BridgeStateMachine {
    param([string]$Path)
    $canvas = New-Canvas -Width 1700 -Height 980
    $g = $canvas.Graphics
    Draw-Title -Graphics $g -Text "ESP32-C3 Bridge State Machine"

    $sc = Get-BoxColors "state"
    $ec = Get-BoxColors "error"
    $wc = Get-BoxColors "warn"

    # States
    Draw-Box -Graphics $g -X 60  -Y 380 -Width 220 -Height 130 -Title "RESET"   -Body "Clear counters`nUSB init`nWait RESET msg" -FillColor $sc.F -LineColor $sc.L
    Draw-Box -Graphics $g -X 360 -Y 380 -Width 220 -Height 130 -Title "INITIALIZE" -Body "Validate config`nStart Wi-Fi SoftAP`nWait CONNECT msg" -FillColor $sc.F -LineColor $sc.L
    Draw-Box -Graphics $g -X 660 -Y 380 -Width 220 -Height 130 -Title "CONNECT" -Body "Accept TCP client`nBegin keepalive`nForward DATA" -FillColor $sc.F -LineColor $sc.L
    Draw-Box -Graphics $g -X 960 -Y 220 -Width 260 -Height 130 -Title "KEEPALIVE`nSERVER_SEND" -Body "Send KA to client`nStart 300 ms window" -FillColor $sc.F -LineColor $sc.L
    Draw-Box -Graphics $g -X 960 -Y 420 -Width 260 -Height 130 -Title "KEEPALIVE`nCLIENT_RETURN" -Body "Await KA reply`n450 ms timeout" -FillColor $sc.F -LineColor $sc.L
    Draw-Box -Graphics $g -X 660 -Y 680 -Width 220 -Height 120 -Title "DISCONNECT" -Body "Controlled teardown`nClose TCP / Wi-Fi" -FillColor $wc.F -LineColor $wc.L
    Draw-Box -Graphics $g -X 300 -Y 680 -Width 260 -Height 120 -Title "ERROR"    -Body "Latch fault reason`nNotify USB host`nWait reset/re-init" -FillColor $ec.F -LineColor $ec.L

    # Arrows
    Draw-Arrow -Graphics $g -X1 280  -Y1 445 -X2 360  -Y2 445 -Label "RESET msg"
    Draw-Arrow -Graphics $g -X1 580  -Y1 445 -X2 660  -Y2 445 -Label "CONNECT msg"
    Draw-Arrow -Graphics $g -X1 880  -Y1 430 -X2 960  -Y2 285 -Label "TCP client"
    Draw-Arrow -Graphics $g -X1 1090 -Y1 350 -X2 1090 -Y2 420 -Label "sent"
    Draw-Arrow -Graphics $g -X1 1090 -Y1 550 -X2 1090 -Y2 285 -Label "reply OK"
    Draw-Arrow -Graphics $g -X1 960  -Y1 485 -X2 880  -Y2 485 -Label "DISCONNECT"
    Draw-Arrow -Graphics $g -X1 770  -Y1 510 -X2 770  -Y2 680 -Label ""
    Draw-Arrow -Graphics $g -X1 430  -Y1 800 -X2 170  -Y2 470 -Label "re-enter"
    Draw-Arrow -Graphics $g -X1 960  -Y1 485 -X2 560  -Y2 740 -Label "fault"
    Draw-Arrow -Graphics $g -X1 300  -Y1 740 -X2 170  -Y2 470 -Label "recover"

    # Retry annotation
    Draw-Label -Graphics $g -Text "Max 3 keepalive retries before ERROR" -X 1000 -Y 600 -Size 11

    Save-Canvas -Canvas $canvas -Path $Path
}

# ===========================================================================
# Diagram 4 - Frame Format
# ===========================================================================
function Draw-FrameFormat {
    param([string]$Path)
    $canvas = New-Canvas -Width 1700 -Height 660
    $g = $canvas.Graphics
    Draw-Title -Graphics $g -Text "USB / TCP Frame Format (Binary, Little-Endian)"

    $fields = @(
        @{ X=40;  W=120; Name="SOF[0]`n0xA5";   Detail="1 B" }
        @{ X=170; W=120; Name="SOF[1]`n0x5A";   Detail="1 B" }
        @{ X=300; W=160; Name="MsgType`nuint8";  Detail="1 B" }
        @{ X=470; W=200; Name="HostLive`nuint32";Detail="4 B" }
        @{ X=680; W=200; Name="DeviceLive`nuint32";Detail="4 B" }
        @{ X=890; W=160; Name="Sequence`nuint16";Detail="2 B" }
        @{ X=1060;W=160; Name="PayloadLen`nuint16";Detail="2 B" }
        @{ X=1230;W=200; Name="CRC16`nuint16";   Detail="2 B" }
        @{ X=1440;W=240; Name="Payload`n0..600 B";Detail="variable" }
    )

    $fc = Get-BoxColors "fw"
    foreach ($f in $fields) {
        Draw-Box -Graphics $g -X $f.X -Y 180 -Width $f.W -Height 200 `
            -Title $f.Name -Body $f.Detail `
            -FillColor $fc.F -LineColor $fc.L
    }

    Draw-Label -Graphics $g -Text "Total fixed overhead: 15 B header + 2 B CRC = 17 B   /   Max frame: 617 B   /   CRC covers all fields except CRC itself" `
               -X 40 -Y 420 -Size 12
    Draw-Label -Graphics $g -Text "Message types:  1=RESET  2=INITIALIZE  3=CONNECT  4=DISCONNECT  5=KEEPALIVE  6=ERROR  7=ACK  8=DATA" `
               -X 40 -Y 460 -Size 12
    Draw-Label -Graphics $g -Text "DATA downlink magic: 0xD0   /   DATA uplink magic: 0xD1   /   Downlink payload: 535 B   /   Uplink payload: 539 B" `
               -X 40 -Y 500 -Size 12

    Save-Canvas -Canvas $canvas -Path $Path
}

# ===========================================================================
# Diagram 5 - Main Loop Flowchart
# ===========================================================================
function Draw-MainLoopFlowchart {
    param([string]$Path)
    $canvas = New-Canvas -Width 900 -Height 1060
    $g = $canvas.Graphics
    Draw-Title -Graphics $g -Text "Bridge Main Loop (20 ms poll)"

    $fw = Get-BoxColors "fw"
    $ec = Get-BoxColors "error"
    $da = Get-BoxColors "data"

    $fw = Get-BoxColors "fw"
    $hw = Get-BoxColors "hw"

    Draw-Box -Graphics $g -X 250 -Y 90  -Width 380 -Height 70  -Title "communication_functions_run" -Body "" -FillColor $fw.F -LineColor $fw.L
    Draw-Arrow -Graphics $g -X1 440 -Y1 160  -X2 440 -Y2 200
    Draw-Box -Graphics $g -X 250 -Y 200 -Width 380 -Height 80  -Title "USB JTAG init" -Body "usb_serial_jtag_driver_install" -FillColor $fw.F -LineColor $fw.L
    Draw-Arrow -Graphics $g -X1 440 -Y1 280  -X2 440 -Y2 320
    Draw-Box -Graphics $g -X 250 -Y 320 -Width 380 -Height 80  -Title "Wi-Fi and netif init" -Body "nvs_flash + esp_netif + esp_wifi" -FillColor $fw.F -LineColor $fw.L
    Draw-Arrow -Graphics $g -X1 440 -Y1 400  -X2 440 -Y2 440
    Draw-Box -Graphics $g -X 250 -Y 440 -Width 380 -Height 80  -Title "TCP server init" -Body "socket + bind + listen" -FillColor $fw.F -LineColor $fw.L
    Draw-Arrow -Graphics $g -X1 440 -Y1 520  -X2 440 -Y2 560
    Draw-Box -Graphics $g -X 250 -Y 560 -Width 380 -Height 80  -Title "Poll USB RX buffer" -Body "Read all available bytes" -FillColor $fw.F -LineColor $fw.L
    Draw-Arrow -Graphics $g -X1 440 -Y1 640  -X2 440 -Y2 680
    Draw-Box -Graphics $g -X 250 -Y 680 -Width 380 -Height 80  -Title "Process complete frames" -Body "Decode, validate CRC, dispatch" -FillColor $fw.F -LineColor $fw.L
    Draw-Arrow -Graphics $g -X1 440 -Y1 760  -X2 440 -Y2 800
    Draw-Box -Graphics $g -X 250 -Y 800 -Width 380 -Height 80  -Title "Service keepalive engine" -Body "Timeout + retry logic" -FillColor $fw.F -LineColor $fw.L
    Draw-Arrow -Graphics $g -X1 440 -Y1 880  -X2 440 -Y2 920
    Draw-Box -Graphics $g -X 250 -Y 920 -Width 380 -Height 80  -Title "vTaskDelay 20 ms" -Body "Yield to FreeRTOS scheduler" -FillColor $hw.F -LineColor $hw.L

    # Loop back arrow
    Draw-Arrow -Graphics $g -X1 250 -Y1 960 -X2 200 -Y2 600 -Label "loop"
    Draw-Arrow -Graphics $g -X1 200 -Y1 600 -X2 250 -Y2 600

    Save-Canvas -Canvas $canvas -Path $Path
}

# ===========================================================================
# Diagram 6 - Keepalive Sequence
# ===========================================================================
function Draw-KeepaliveSequence {
    param([string]$Path)
    $canvas = New-Canvas -Width 1400 -Height 700
    $g = $canvas.Graphics
    Draw-Title -Graphics $g -Text "Keepalive Protocol Sequence"

    $py = Get-BoxColors "python"
    $fw = Get-BoxColors "fw"
    $hw = Get-BoxColors "hw"

    Draw-Box -Graphics $g -X  40 -Y 120 -Width 260 -Height 80 -Title "Python Server"    -Body "Controls session"  -FillColor $py.F -LineColor $py.L
    Draw-Box -Graphics $g -X 560 -Y 120 -Width 260 -Height 80 -Title "ESP32-C3 Bridge"  -Body "Low-level watchdog" -FillColor $fw.F -LineColor $fw.L
    Draw-Box -Graphics $g -X 1080 -Y 120 -Width 260 -Height 80 -Title "ESP32-S3 Client"  -Body "TCP peer"           -FillColor $hw.F -LineColor $hw.L

    Draw-Arrow -Graphics $g -X1 430  -Y1 250 -X2 560  -Y2 250 -Label "KEEPALIVE ServerLiveInt even, request_id"
    Draw-Arrow -Graphics $g -X1 820  -Y1 340 -X2 1080 -Y2 340 -Label "forward over TCP"
    Draw-Arrow -Graphics $g -X1 1080 -Y1 420 -X2 820  -Y2 420 -Label "KEEPALIVE reply ClientLiveInt odd"
    Draw-Arrow -Graphics $g -X1 560  -Y1 510 -X2 430  -Y2 510 -Label "ACK to USB host, telemetry update"

    Draw-Label -Graphics $g -Text "Keepalive period: 300 ms   /   Client reply window: 450 ms   /   Max retries: 3  =>  ERROR" `
               -X 40 -Y 600 -Size 12

    Save-Canvas -Canvas $canvas -Path $Path
}

# ===========================================================================
# Diagram 7 - Data Forwarding Flow
# ===========================================================================
function Draw-DataForwardingFlow {
    param([string]$Path)
    $canvas = New-Canvas -Width 1600 -Height 600
    $g = $canvas.Graphics
    Draw-Title -Graphics $g -Text "Binary DATA Forwarding - Downlink and Uplink"

    $py = Get-BoxColors "python"
    $fw = Get-BoxColors "fw"
    $hw = Get-BoxColors "hw"
    $da = Get-BoxColors "data"

    Draw-Box -Graphics $g -X  40 -Y 140 -Width 280 -Height 160 -Title "Python Server" `
             -Body "data_payload.py`nDownlink thread 2 Hz`n535-byte payload`n100f + 20i + 50s" -FillColor $py.F -LineColor $py.L

    Draw-Box -Graphics $g -X 480 -Y 100 -Width 320 -Height 240 -Title "ESP32-C3 Bridge" `
             -Body "Receive DATA frame from USB`nValidate CRC`nForward payload over TCP`nReceive uplink from TCP`nWrap in DATA frame`nForward over USB" -FillColor $fw.F -LineColor $fw.L

    Draw-Box -Graphics $g -X 1000 -Y 140 -Width 280 -Height 160 -Title "ESP32-S3 Client" `
             -Body "Consume downlink`nProduce uplink`n539-byte payload`n100f + 20i + 50s" -FillColor $hw.F -LineColor $hw.L

    Draw-Box -Graphics $g -X  40 -Y 380 -Width 280 -Height 120 -Title "Downlink magic" `
             -Body "0xD0 in payload[0]`nseq uint32 LE" -FillColor $da.F -LineColor $da.L
    Draw-Box -Graphics $g -X 1000 -Y 380 -Width 280 -Height 120 -Title "Uplink magic" `
             -Body "0xD1 in payload[0]`nseq + timestamp_ms" -FillColor $da.F -LineColor $da.L

    Draw-Arrow -Graphics $g -X1 320  -Y1 220 -X2 480  -Y2 220 -Label "USB DATA frame"
    Draw-Arrow -Graphics $g -X1 800  -Y1 220 -X2 1000 -Y2 220 -Label "TCP forward"
    Draw-Arrow -Graphics $g -X1 1000 -Y1 280 -X2 800  -Y2 280 -Label "TCP uplink"
    Draw-Arrow -Graphics $g -X1 480  -Y1 280 -X2 320  -Y2 280 -Label "USB DATA frame"

    Save-Canvas -Canvas $canvas -Path $Path
}

# ===========================================================================
# Diagram 8 - Memory Layout
# ===========================================================================
function Draw-MemoryLayout {
    param([string]$Path)
    $canvas = New-Canvas -Width 1600 -Height 780
    $g = $canvas.Graphics
    Draw-Title -Graphics $g -Text "ESP32-C3 Bridge - Key Static Memory Allocations"

    $fw = Get-BoxColors "fw"
    $hw = Get-BoxColors "hw"

    Draw-Box -Graphics $g -X 40 -Y 120 -Width 360 -Height 280 `
        -Title "USB RX/TX Buffers" `
        -Body "s_usb_rx_buf: 1024 B (stack)`ns_usb_tx_buf: 1024 B (stack)`nUSB driver RX FIFO: driver-managed`nUSB driver TX FIFO: driver-managed" `
        -FillColor $fw.F -LineColor $fw.L

    Draw-Box -Graphics $g -X 440 -Y 120 -Width 360 -Height 280 `
        -Title "TCP Buffers" `
        -Body "s_tcp_rx_buffer: 1280 B (static)`nFrame assembly in-place`nMax payload: 600 B`nMax frame: 617 B" `
        -FillColor $fw.F -LineColor $fw.L

    Draw-Box -Graphics $g -X 840 -Y 120 -Width 360 -Height 280 `
        -Title "Frame Struct" `
        -Body "bridge_frame_t (static)`n  message_type: uint8`n  host_live: uint32`n  device_live: uint32`n  sequence: uint16`n  payload_length: uint16`n  payload[600]: uint8" `
        -FillColor $fw.F -LineColor $fw.L

    Draw-Box -Graphics $g -X 1240 -Y 120 -Width 320 -Height 280 `
        -Title "Telemetry Counters" `
        -Body "s_transport_last_delay_ms`ns_transport_max_delay_ms`ns_total_error_count`ns_keepalive_empty_window_count`ns_timeout_event_count`ns_active_session_id" `
        -FillColor $hw.F -LineColor $hw.L

    Draw-Box -Graphics $g -X 40 -Y 440 -Width 760 -Height 200 `
        -Title "Stack - single FreeRTOS task" `
        -Body "USB RX local buffer   /   TX scratch buffer   /   Frame decode workspace`nAll on the single communication task stack - no heap allocation in hot path" `
        -FillColor $fw.F -LineColor $fw.L

    Draw-Box -Graphics $g -X 840 -Y 440 -Width 720 -Height 200 `
        -Title "Wi-Fi and lwIP - ESP-IDF managed heap" `
        -Body "Wi-Fi driver buffers   /   lwIP TX/RX pbuf pool`nTCP socket receive buffer   /   netif descriptor`nAll managed by ESP-IDF memory allocator" `
        -FillColor $hw.F -LineColor $hw.L

    Draw-Label -Graphics $g -Text "Total static data allocations: ~4.5 KB   /   FreeRTOS task stack: 4 KB   /   Total firmware flash footprint: <300 KB" `
               -X 40 -Y 680 -Size 12

    Save-Canvas -Canvas $canvas -Path $Path
}

# ===========================================================================
# Diagram 9 - Error Recovery Map
# ===========================================================================
function Draw-ErrorRecovery {
    param([string]$Path)
    $canvas = New-Canvas -Width 1700 -Height 900
    $g = $canvas.Graphics
    Draw-Title -Graphics $g -Text "Error Sources and Recovery Paths"

    $ec = Get-BoxColors "error"
    $sc = Get-BoxColors "state"
    $wc = Get-BoxColors "warn"

    # Fault sources (left column)
    $faults = @(
        @{ Y=140; T="CRC Mismatch";       B="Bad frame integrity`nDrop frame, count error" }
        @{ Y=290; T="Keepalive Timeout";  B="Client silent >450 ms`nRetry up to 3 times" }
        @{ Y=440; T="Running Int Fail";   B="ServerLive stalled`nAssume host stuck" }
        @{ Y=590; T="TCP Socket Error";   B="accept/recv/send fail`nClose client, re-listen" }
        @{ Y=740; T="USB Read Error";     B="usb_serial_jtag_read fail`nLog and continue" }
    )

    foreach ($f in $faults) {
        Draw-Box -Graphics $g -X 60 -Y $f.Y -Width 310 -Height 110 `
            -Title $f.T -Body $f.B -FillColor $ec.F -LineColor $ec.L
        Draw-Arrow -Graphics $g -X1 370 -Y1 ($f.Y+55) -X2 540 -Y2 490
    }

    # ERROR state (center)
    Draw-Box -Graphics $g -X 540 -Y 410 -Width 280 -Height 160 `
        -Title "ERROR state" `
        -Body "Latch fault reason`nNotify USB host`nStop data stream`nWait for operator" `
        -FillColor $ec.F -LineColor $ec.L

    # Recovery options (right column)
    Draw-Box -Graphics $g -X 1000 -Y 260 -Width 280 -Height 120 `
        -Title "RESET recovery" -Body "Full state clear`nRestart bridge" -FillColor $sc.F -LineColor $sc.L
    Draw-Box -Graphics $g -X 1000 -Y 450 -Width 280 -Height 120 `
        -Title "INITIALIZE recovery" -Body "Re-prepare link`nKeep Wi-Fi stack" -FillColor $sc.F -LineColor $sc.L
    Draw-Box -Graphics $g -X 1000 -Y 640 -Width 280 -Height 120 `
        -Title "DISCONNECT" -Body "Controlled teardown`nReturn to RESET" -FillColor $wc.F -LineColor $wc.L

    Draw-Arrow -Graphics $g -X1 820 -Y1 460 -X2 1000 -Y2 320 -Label "hard reset"
    Draw-Arrow -Graphics $g -X1 820 -Y1 490 -X2 1000 -Y2 510 -Label "soft re-init"
    Draw-Arrow -Graphics $g -X1 820 -Y1 530 -X2 1000 -Y2 700 -Label "graceful stop"

    Draw-Label -Graphics $g -Text "SO_SNDTIMEO = 50 ms on TCP send socket prevents bridge task stall during TCP window closure" `
               -X 60 -Y 840 -Size 12

    Save-Canvas -Canvas $canvas -Path $Path
}

# ===========================================================================
# Entry point
# ===========================================================================
function New-BridgeDesignDiagramSet {
    if (Test-Path $OutputDir) { Remove-Item $OutputDir -Recurse -Force }
    New-Item -ItemType Directory -Force $OutputDir | Out-Null

    Write-Host "Rendering bridge design diagrams to: $OutputDir"

    Draw-HWTopology            -Path (Join-Path $OutputDir "bridge_hw_topology.png")
    Write-Host "  [1/9] bridge_hw_topology.png"

    Draw-SWArchitecture        -Path (Join-Path $OutputDir "bridge_sw_architecture.png")
    Write-Host "  [2/9] bridge_sw_architecture.png"

    Draw-BridgeStateMachine    -Path (Join-Path $OutputDir "bridge_state_machine.png")
    Write-Host "  [3/9] bridge_state_machine.png"

    Draw-FrameFormat           -Path (Join-Path $OutputDir "bridge_frame_format.png")
    Write-Host "  [4/9] bridge_frame_format.png"

    Draw-MainLoopFlowchart     -Path (Join-Path $OutputDir "bridge_main_loop.png")
    Write-Host "  [5/9] bridge_main_loop.png"

    Draw-KeepaliveSequence     -Path (Join-Path $OutputDir "bridge_keepalive_sequence.png")
    Write-Host "  [6/9] bridge_keepalive_sequence.png"

    Draw-DataForwardingFlow    -Path (Join-Path $OutputDir "bridge_data_forwarding.png")
    Write-Host "  [7/9] bridge_data_forwarding.png"

    Draw-MemoryLayout          -Path (Join-Path $OutputDir "bridge_memory_layout.png")
    Write-Host "  [8/9] bridge_memory_layout.png"

    Draw-ErrorRecovery         -Path (Join-Path $OutputDir "bridge_error_recovery.png")
    Write-Host "  [9/9] bridge_error_recovery.png"

    Write-Host ""
    Get-ChildItem $OutputDir | Select-Object Name, Length
}

New-BridgeDesignDiagramSet
