[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$SoundFile
)

# @brief Play one WAV wait sound synchronously.
# @details Kept in a dedicated helper so the repeating wait worker can spawn a
# clean playback process for each repeat instead of owning the audio lifetime.
Add-Type -AssemblyName System
$player = New-Object System.Media.SoundPlayer $SoundFile
$player.PlaySync()
