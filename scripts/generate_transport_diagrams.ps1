<#
.SYNOPSIS
Renders the simulator transport architecture diagrams used by the design docs.

.DESCRIPTION
Uses `System.Drawing` to generate the maintained PNG diagrams for the split
architecture, state machine, failure overview, and packet flows. The generated
images are later embedded into the detailed design `.docx`.
#>
[CmdletBinding()]
param()

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$OutputDir = Join-Path $ProjectRoot "docs\diagrams"

Add-Type -AssemblyName System.Drawing

# @brief Create a rounded rectangle graphics path.
# @details Builds the path manually because older System.Drawing runtimes do
# not expose DrawRoundedRectangle or FillRoundedRectangle helpers.
# @param[in] X Left coordinate.
# @param[in] Y Top coordinate.
# @param[in] Width Rectangle width.
# @param[in] Height Rectangle height.
# @param[in] Radius Corner radius in pixels.
# @return GraphicsPath instance for the requested rounded rectangle.
function New-RoundedRectanglePath {
    param(
        [Parameter(Mandatory = $true)]
        [float]$X,
        [Parameter(Mandatory = $true)]
        [float]$Y,
        [Parameter(Mandatory = $true)]
        [float]$Width,
        [Parameter(Mandatory = $true)]
        [float]$Height,
        [Parameter(Mandatory = $true)]
        [float]$Radius
    )

    $diameter = $Radius * 2
    $path = New-Object System.Drawing.Drawing2D.GraphicsPath
    $path.AddArc($X, $Y, $diameter, $diameter, 180, 90)
    $path.AddArc($X + $Width - $diameter, $Y, $diameter, $diameter, 270, 90)
    $path.AddArc($X + $Width - $diameter, $Y + $Height - $diameter, $diameter, $diameter, 0, 90)
    $path.AddArc($X, $Y + $Height - $diameter, $diameter, $diameter, 90, 90)
    $path.CloseFigure()
    return $path
}

# @brief Create a new diagram canvas.
# @details Applies a dark high-tech background and enables anti-aliased
# rendering so the exported PNGs remain readable inside Word.
# @param[in] Width Bitmap width in pixels.
# @param[in] Height Bitmap height in pixels.
# @return Hashtable with Bitmap, Graphics, Width, and Height.
function New-Canvas {
    param(
        [int]$Width = 1400,
        [int]$Height = 800
    )

    $bitmap = New-Object System.Drawing.Bitmap $Width, $Height
    $graphics = [System.Drawing.Graphics]::FromImage($bitmap)
    $graphics.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
    $graphics.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
    $graphics.PixelOffsetMode = [System.Drawing.Drawing2D.PixelOffsetMode]::HighQuality
    $graphics.TextRenderingHint = [System.Drawing.Text.TextRenderingHint]::ClearTypeGridFit
    $graphics.Clear([System.Drawing.Color]::FromArgb(8, 16, 28))
    return @{
        Bitmap = $bitmap
        Graphics = $graphics
        Width = $Width
        Height = $Height
    }
}

# @brief Save and dispose of a diagram canvas.
# @details Persists the PNG then releases GDI resources.
# @param[in] Canvas Canvas created by New-Canvas.
# @param[in] Path Output PNG path.
function Save-Canvas {
    param(
        [Parameter(Mandatory = $true)]
        [hashtable]$Canvas,
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    $Canvas.Bitmap.Save($Path, [System.Drawing.Imaging.ImageFormat]::Png)
    $Canvas.Graphics.Dispose()
    $Canvas.Bitmap.Dispose()
}

# @brief Draw a standard diagram title.
# @details Uses bright contrast so the title remains readable after Word image
# downscaling.
# @param[in] Graphics Active Graphics instance.
# @param[in] Text Title text.
function Draw-Title {
    param(
        [Parameter(Mandatory = $true)]
        [System.Drawing.Graphics]$Graphics,
        [Parameter(Mandatory = $true)]
        [string]$Text
    )

    $font = New-Object System.Drawing.Font("Arial", 24, [System.Drawing.FontStyle]::Bold)
    $brush = New-Object System.Drawing.SolidBrush([System.Drawing.Color]::FromArgb(232, 247, 255))
    try {
        $Graphics.DrawString($Text, $font, $brush, 40, 24)
    } finally {
        $brush.Dispose()
        $font.Dispose()
    }
}

# @brief Draw a labeled rounded rectangle.
# @details This is the basic visual primitive used for actors, states, and
# transport blocks in all rendered diagrams.
# @param[in] Graphics Active Graphics instance.
# @param[in] X Left coordinate.
# @param[in] Y Top coordinate.
# @param[in] Width Rectangle width.
# @param[in] Height Rectangle height.
# @param[in] Title Box title.
# @param[in] Body Box body text.
# @param[in] FillColor Background color.
# @param[in] LineColor Border color.
function Draw-Box {
    param(
        [Parameter(Mandatory = $true)]
        [System.Drawing.Graphics]$Graphics,
        [Parameter(Mandatory = $true)]
        [float]$X,
        [Parameter(Mandatory = $true)]
        [float]$Y,
        [Parameter(Mandatory = $true)]
        [float]$Width,
        [Parameter(Mandatory = $true)]
        [float]$Height,
        [Parameter(Mandatory = $true)]
        [string]$Title,
        [Parameter(Mandatory = $true)]
        [string]$Body,
        [Parameter(Mandatory = $true)]
        [System.Drawing.Color]$FillColor,
        [Parameter(Mandatory = $true)]
        [System.Drawing.Color]$LineColor
    )

    $path = New-RoundedRectanglePath -X $X -Y $Y -Width $Width -Height $Height -Radius 22
    $fillBrush = New-Object System.Drawing.SolidBrush($FillColor)
    $linePen = New-Object System.Drawing.Pen($LineColor, 3)
    $titleFont = New-Object System.Drawing.Font("Arial", 16, [System.Drawing.FontStyle]::Bold)
    $bodyFont = New-Object System.Drawing.Font("Consolas", 12, [System.Drawing.FontStyle]::Regular)
    $titleBrush = New-Object System.Drawing.SolidBrush([System.Drawing.Color]::FromArgb(236, 250, 255))
    $bodyBrush = New-Object System.Drawing.SolidBrush([System.Drawing.Color]::FromArgb(196, 229, 240))
    $bodyRect = New-Object System.Drawing.RectangleF ($X + 18), ($Y + 48), ($Width - 36), ($Height - 60)
    try {
        $Graphics.FillPath($fillBrush, $path)
        $Graphics.DrawPath($linePen, $path)
        $Graphics.DrawString($Title, $titleFont, $titleBrush, $X + 18, $Y + 14)
        $Graphics.DrawString($Body, $bodyFont, $bodyBrush, $bodyRect)
    } finally {
        $bodyBrush.Dispose()
        $titleBrush.Dispose()
        $bodyFont.Dispose()
        $titleFont.Dispose()
        $linePen.Dispose()
        $fillBrush.Dispose()
        $path.Dispose()
    }
}

# @brief Draw a directional arrow with an optional label.
# @details Uses a consistent cyan accent so flow direction is clear in dark
# themed diagrams.
# @param[in] Graphics Active Graphics instance.
# @param[in] X1 Start X coordinate.
# @param[in] Y1 Start Y coordinate.
# @param[in] X2 End X coordinate.
# @param[in] Y2 End Y coordinate.
# @param[in] Label Optional label text.
function Draw-Arrow {
    param(
        [Parameter(Mandatory = $true)]
        [System.Drawing.Graphics]$Graphics,
        [Parameter(Mandatory = $true)]
        [float]$X1,
        [Parameter(Mandatory = $true)]
        [float]$Y1,
        [Parameter(Mandatory = $true)]
        [float]$X2,
        [Parameter(Mandatory = $true)]
        [float]$Y2,
        [string]$Label = ""
    )

    $pen = New-Object System.Drawing.Pen([System.Drawing.Color]::FromArgb(94, 228, 255), 4)
    $cap = New-Object System.Drawing.Drawing2D.AdjustableArrowCap(6, 8, $true)
    $pen.CustomEndCap = $cap
    try {
        $Graphics.DrawLine($pen, $X1, $Y1, $X2, $Y2)
        if (-not [string]::IsNullOrWhiteSpace($Label)) {
            $font = New-Object System.Drawing.Font("Arial", 12, [System.Drawing.FontStyle]::Bold)
            $brush = New-Object System.Drawing.SolidBrush([System.Drawing.Color]::FromArgb(172, 235, 255))
            try {
                $midX = (($X1 + $X2) / 2) - 46
                $midY = (($Y1 + $Y2) / 2) - 24
                $Graphics.DrawString($Label, $font, $brush, $midX, $midY)
            } finally {
                $brush.Dispose()
                $font.Dispose()
            }
        }
    } finally {
        $cap.Dispose()
        $pen.Dispose()
    }
}

# @brief Draw a packet flow diagram.
# @details Renders a left-to-right sequence of actors and transitions for one
# packet type.
# @param[in] Title Diagram title.
# @param[in] Steps Ordered packet flow steps.
# @param[in] Path Output PNG path.
function Draw-FlowDiagram {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Title,
        [Parameter(Mandatory = $true)]
        [object[]]$Steps,
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    $canvas = New-Canvas -Width 1600 -Height 560
    $graphics = $canvas.Graphics
    Draw-Title -Graphics $graphics -Text $Title

    $x = 60
    foreach ($index in 0..($Steps.Count - 1)) {
        $step = $Steps[$index]
        Draw-Box -Graphics $graphics -X $x -Y 190 -Width 260 -Height 150 `
            -Title $step.Title -Body $step.Body `
            -FillColor ([System.Drawing.Color]::FromArgb(255, 14, 38, 62)) `
            -LineColor ([System.Drawing.Color]::FromArgb(255, 82, 205, 255))
        if ($index -lt ($Steps.Count - 1)) {
            Draw-Arrow -Graphics $graphics -X1 ($x + 260) -Y1 265 -X2 ($x + 340) -Y2 265 -Label $step.Edge
        }
        $x += 340
    }

    Save-Canvas -Canvas $canvas -Path $Path
}

# @brief Draw the transport split architecture diagram.
# @details Shows the PC host, USB bridge, ESP32-C3 transport controller, Wi-Fi
# TCP link, and ESP32-S3 client responsibilities.
# @param[in] Path Output PNG path.
function Draw-Architecture {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    $canvas = New-Canvas -Width 1700 -Height 780
    $graphics = $canvas.Graphics
    Draw-Title -Graphics $graphics -Text "Planned Split Architecture: PC Simulator, ESP32-C3 Bridge, ESP32-S3 Client"
    Draw-Box -Graphics $graphics -X 60 -Y 210 -Width 360 -Height 220 `
        -Title "PC Simulator Application" `
        -Body "UI and operator controls`nSimulator logic`nAuthoritative HostLiveInteger`nReset/init policy" `
        -FillColor ([System.Drawing.Color]::FromArgb(255, 16, 44, 70)) `
        -LineColor ([System.Drawing.Color]::FromArgb(255, 91, 230, 255))
    Draw-Box -Graphics $graphics -X 500 -Y 210 -Width 260 -Height 220 `
        -Title "USB COM Link" `
        -Body "Framed low-level packets`nCRC protected`nBidirectional keep-alive" `
        -FillColor ([System.Drawing.Color]::FromArgb(255, 20, 36, 54)) `
        -LineColor ([System.Drawing.Color]::FromArgb(255, 120, 200, 255))
    Draw-Box -Graphics $graphics -X 840 -Y 180 -Width 360 -Height 280 `
        -Title "ESP32-C3-SuperMini Transport Controller" `
        -Body "USB session owner`nWi-Fi/TCP bridge`nLow-level watchdog`nCRC validation`nreset / initialize / connect / disconnect / error" `
        -FillColor ([System.Drawing.Color]::FromArgb(255, 18, 54, 74)) `
        -LineColor ([System.Drawing.Color]::FromArgb(255, 87, 245, 255))
    Draw-Box -Graphics $graphics -X 1280 -Y 210 -Width 300 -Height 220 `
        -Title "Wi-Fi TCP Link" `
        -Body "Dedicated keep-alive path`nApplication DATA after validation`nSingle active client session" `
        -FillColor ([System.Drawing.Color]::FromArgb(255, 20, 36, 54)) `
        -LineColor ([System.Drawing.Color]::FromArgb(255, 120, 200, 255))
    Draw-Box -Graphics $graphics -X 60 -Y 540 -Width 1520 -Height 150 `
        -Title "ESP32-S3 Client Application" `
        -Body "Validated transport layer below controller logic -> CRC + liveness + sequencing checks -> higher-level machine protocol and LVGL UI" `
        -FillColor ([System.Drawing.Color]::FromArgb(255, 16, 40, 62)) `
        -LineColor ([System.Drawing.Color]::FromArgb(255, 91, 230, 255))
    Draw-Arrow -Graphics $graphics -X1 420 -Y1 320 -X2 500 -Y2 320 -Label "frames"
    Draw-Arrow -Graphics $graphics -X1 760 -Y1 320 -X2 840 -Y2 320 -Label "bridge"
    Draw-Arrow -Graphics $graphics -X1 1200 -Y1 320 -X2 1280 -Y2 320 -Label "tcp"
    Draw-Arrow -Graphics $graphics -X1 1420 -Y1 430 -X2 1420 -Y2 540 -Label "validated payloads"
    Draw-Arrow -Graphics $graphics -X1 240 -Y1 540 -X2 240 -Y2 430 -Label "status / responses"
    Save-Canvas -Canvas $canvas -Path $Path
}

# @brief Draw the mirrored low-level state machine.
# @details Shows the shared reset/initialize/connect/disconnect/error contract.
# @param[in] Path Output PNG path.
function Draw-StateDiagram {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    $canvas = New-Canvas -Width 1500 -Height 900
    $graphics = $canvas.Graphics
    Draw-Title -Graphics $graphics -Text "Mirrored Low-Level State Machine"
    Draw-Box -Graphics $graphics -X 100 -Y 320 -Width 220 -Height 120 `
        -Title "reset" -Body "self-test`nclear counters`nload parameters" `
        -FillColor ([System.Drawing.Color]::FromArgb(255, 16, 44, 70)) `
        -LineColor ([System.Drawing.Color]::FromArgb(255, 91, 230, 255))
    Draw-Box -Graphics $graphics -X 420 -Y 320 -Width 220 -Height 120 `
        -Title "initialize" -Body "validate config`nprepare resources" `
        -FillColor ([System.Drawing.Color]::FromArgb(255, 16, 44, 70)) `
        -LineColor ([System.Drawing.Color]::FromArgb(255, 91, 230, 255))
    Draw-Box -Graphics $graphics -X 740 -Y 320 -Width 220 -Height 120 `
        -Title "connect" -Body "open / maintain session`n100 mSec keep-alive" `
        -FillColor ([System.Drawing.Color]::FromArgb(255, 16, 44, 70)) `
        -LineColor ([System.Drawing.Color]::FromArgb(255, 91, 230, 255))
    Draw-Box -Graphics $graphics -X 1060 -Y 150 -Width 220 -Height 120 `
        -Title "disconnect" -Body "controlled shutdown`nrelease resources" `
        -FillColor ([System.Drawing.Color]::FromArgb(255, 44, 42, 20)) `
        -LineColor ([System.Drawing.Color]::FromArgb(255, 255, 196, 98))
    Draw-Box -Graphics $graphics -X 1060 -Y 500 -Width 220 -Height 140 `
        -Title "error" -Body "latched transport fault`nwait for reset or initialize" `
        -FillColor ([System.Drawing.Color]::FromArgb(255, 66, 24, 36)) `
        -LineColor ([System.Drawing.Color]::FromArgb(255, 255, 122, 151))
    Draw-Arrow -Graphics $graphics -X1 320 -Y1 380 -X2 420 -Y2 380 -Label "prepare"
    Draw-Arrow -Graphics $graphics -X1 640 -Y1 380 -X2 740 -Y2 380 -Label "go online"
    Draw-Arrow -Graphics $graphics -X1 960 -Y1 350 -X2 1060 -Y2 240 -Label "controlled"
    Draw-Arrow -Graphics $graphics -X1 960 -Y1 410 -X2 1060 -Y2 560 -Label "fault"
    Draw-Arrow -Graphics $graphics -X1 1170 -Y1 270 -X2 210 -Y2 320 -Label "reset"
    Draw-Arrow -Graphics $graphics -X1 1170 -Y1 570 -X2 210 -Y2 380 -Label "recover"
    Draw-Arrow -Graphics $graphics -X1 1170 -Y1 640 -X2 530 -Y2 440 -Label "re-init"
    Save-Canvas -Canvas $canvas -Path $Path
}

# @brief Draw a high-level failure and recovery map.
# @details Aggregates the main low-level faults and the permitted recovery
# actions from the error state.
# @param[in] Path Output PNG path.
function Draw-FailureOverview {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    $canvas = New-Canvas -Width 1650 -Height 900
    $graphics = $canvas.Graphics
    Draw-Title -Graphics $graphics -Text "Failure Modes and Recovery Overview"
    Draw-Box -Graphics $graphics -X 80 -Y 140 -Width 310 -Height 120 `
        -Title "CRC Failure" -Body "Bad frame integrity`nDrop payload, enter error" `
        -FillColor ([System.Drawing.Color]::FromArgb(255, 66, 24, 36)) `
        -LineColor ([System.Drawing.Color]::FromArgb(255, 255, 122, 151))
    Draw-Box -Graphics $graphics -X 80 -Y 300 -Width 310 -Height 120 `
        -Title "Host Watchdog Failure" -Body "HostLiveInteger stalled`nAssume PC host is stuck" `
        -FillColor ([System.Drawing.Color]::FromArgb(255, 66, 24, 36)) `
        -LineColor ([System.Drawing.Color]::FromArgb(255, 255, 122, 151))
    Draw-Box -Graphics $graphics -X 80 -Y 460 -Width 310 -Height 120 `
        -Title "Device Watchdog Failure" -Body "No keep-alive ACK or device progress" `
        -FillColor ([System.Drawing.Color]::FromArgb(255, 66, 24, 36)) `
        -LineColor ([System.Drawing.Color]::FromArgb(255, 255, 122, 151))
    Draw-Box -Graphics $graphics -X 80 -Y 620 -Width 310 -Height 120 `
        -Title "Transport Faults" -Body "USB loss`nWi-Fi association failure`nTCP session loss" `
        -FillColor ([System.Drawing.Color]::FromArgb(255, 66, 24, 36)) `
        -LineColor ([System.Drawing.Color]::FromArgb(255, 255, 122, 151))
    Draw-Box -Graphics $graphics -X 560 -Y 330 -Width 260 -Height 180 `
        -Title "error" -Body "Freeze normal forwarding`nPreserve fault reason`nWait for reset or initialize" `
        -FillColor ([System.Drawing.Color]::FromArgb(255, 66, 24, 36)) `
        -LineColor ([System.Drawing.Color]::FromArgb(255, 255, 122, 151))
    Draw-Box -Graphics $graphics -X 980 -Y 200 -Width 260 -Height 120 `
        -Title "reset" -Body "full low-level reset" `
        -FillColor ([System.Drawing.Color]::FromArgb(255, 16, 44, 70)) `
        -LineColor ([System.Drawing.Color]::FromArgb(255, 91, 230, 255))
    Draw-Box -Graphics $graphics -X 980 -Y 470 -Width 260 -Height 120 `
        -Title "initialize" -Body "re-prepare link resources" `
        -FillColor ([System.Drawing.Color]::FromArgb(255, 16, 44, 70)) `
        -LineColor ([System.Drawing.Color]::FromArgb(255, 91, 230, 255))
    Draw-Arrow -Graphics $graphics -X1 390 -Y1 200 -X2 560 -Y2 390 -Label "to error"
    Draw-Arrow -Graphics $graphics -X1 390 -Y1 360 -X2 560 -Y2 390
    Draw-Arrow -Graphics $graphics -X1 390 -Y1 520 -X2 560 -Y2 420
    Draw-Arrow -Graphics $graphics -X1 390 -Y1 680 -X2 560 -Y2 450
    Draw-Arrow -Graphics $graphics -X1 820 -Y1 390 -X2 980 -Y2 260 -Label "hard recover"
    Draw-Arrow -Graphics $graphics -X1 820 -Y1 440 -X2 980 -Y2 530 -Label "soft recover"
    Save-Canvas -Canvas $canvas -Path $Path
}

# @brief Build all rendered transport diagrams for the detailed design docs.
# @details Generates the architecture, state, failure, and per-packet flow PNGs
# into docs/diagrams for later embedding into the docx package.
function New-TransportDiagramSet {
    <#
    @brief Build all rendered transport diagrams for the detailed design docs.
    @details Generates the architecture, state, failure, and per-packet flow PNGs
    into docs/diagrams for later embedding into the docx package.
    #>
    $packetSpecs = @(
        @{
            Name = "packet_reset_flow.png"
            Title = "RESET Packet Flow"
            Steps = @(
                @{ Title = "PC Host"; Body = "Send RESET with profile"; Edge = "ACK" }
                @{ Title = "ESP32-C3"; Body = "Clear state and run self-test"; Edge = "success?" }
                @{ Title = "Supervisor"; Body = "If OK -> initialize"; Edge = "" }
            )
        }
        @{
            Name = "packet_initialize_flow.png"
            Title = "INITIALIZE Packet Flow"
            Steps = @(
                @{ Title = "PC Host"; Body = "Send initialize settings"; Edge = "validate" }
                @{ Title = "ESP32-C3"; Body = "Prepare Wi-Fi/TCP resources"; Edge = "ACK" }
                @{ Title = "Supervisor"; Body = "If OK -> connect"; Edge = "" }
            )
        }
        @{
            Name = "packet_connect_flow.png"
            Title = "CONNECT Packet Flow"
            Steps = @(
                @{ Title = "PC Host"; Body = "Request connect"; Edge = "session" }
                @{ Title = "ESP32-C3"; Body = "Join Wi-Fi and open TCP"; Edge = "ACK" }
                @{ Title = "Connected Link"; Body = "Begin 100 mSec keep-alive"; Edge = "" }
            )
        }
        @{
            Name = "packet_disconnect_flow.png"
            Title = "DISCONNECT Packet Flow"
            Steps = @(
                @{ Title = "PC Host"; Body = "Send disconnect reason"; Edge = "close" }
                @{ Title = "ESP32-C3"; Body = "Controlled teardown"; Edge = "ACK" }
                @{ Title = "Supervisor"; Body = "Return to reset"; Edge = "" }
            )
        }
        @{
            Name = "packet_keepalive_flow.png"
            Title = "KEEPALIVE Packet Flow"
            Steps = @(
                @{ Title = "PC Host"; Body = "Increment HostLiveInteger every 100 mSec"; Edge = "check" }
                @{ Title = "ESP32-C3"; Body = "Validate host progress"; Edge = "reply" }
                @{ Title = "PC Host"; Body = "Verify DeviceLiveInteger and timing"; Edge = "" }
            )
        }
        @{
            Name = "packet_data_flow.png"
            Title = "DATA Packet Flow"
            Steps = @(
                @{ Title = "Upper Layer"; Body = "Build application payload"; Edge = "CRC" }
                @{ Title = "ESP32-C3"; Body = "Validate frame and forward over TCP"; Edge = "response" }
                @{ Title = "Client / Host"; Body = "Consume validated payload"; Edge = "" }
            )
        }
        @{
            Name = "packet_error_flow.png"
            Title = "ERROR Packet Flow"
            Steps = @(
                @{ Title = "Faulting Side"; Body = "Latch low-level fault"; Edge = "report" }
                @{ Title = "Peer"; Body = "Receive ERROR summary"; Edge = "decide" }
                @{ Title = "Supervisor"; Body = "Issue reset or initialize"; Edge = "" }
            )
        }
    )

if (Test-Path $OutputDir) {
        Remove-Item $OutputDir -Recurse -Force
    }

    # Regenerate the full set from scratch so the docx embed step never mixes old and new diagrams.
    New-Item -ItemType Directory -Force $OutputDir | Out-Null
    Draw-Architecture -Path (Join-Path $OutputDir "architecture_transport_split.png")
    Draw-StateDiagram -Path (Join-Path $OutputDir "low_level_state_machine.png")
    Draw-FailureOverview -Path (Join-Path $OutputDir "failure_modes_overview.png")

    foreach ($packetSpec in $packetSpecs) {
        Draw-FlowDiagram -Title $packetSpec.Title -Steps $packetSpec.Steps -Path (Join-Path $OutputDir $packetSpec.Name)
    }
}

New-TransportDiagramSet
Get-ChildItem $OutputDir | Select-Object Name, Length
