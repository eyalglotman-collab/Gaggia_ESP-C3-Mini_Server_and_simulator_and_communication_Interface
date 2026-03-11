# Versioning Policy

## Format
- Version format: `X.Y.Z`

## Meaning
- `X` (major): architecture changes and large feature-scope changes.
- `Y` (minor): bug fixes and incremental functionality additions.
- `Z` (patch/sub-version): accepted successful verification cycles.

## Version Tree
- Application version source: repository root `VERSION`
- Firmware version source: `firmware/esp32c3_bridge/VERSION`

## Firmware Rule
- The ESP32-C3 bridge firmware uses the same `X.Y.Z` versioning rules as the simulator application.
- Firmware-facing design notes, README guidance, splash metadata, and firmware-source comments must stay aligned with `firmware/esp32c3_bridge/VERSION`.

## Operational Rule
- After each successful local verification cycle, confirm whether to:
  1. commit code changes
  2. create a sub-version (`Z = Z + 1`)

## Current Version Sources
- Canonical application version is stored in repository root file: `VERSION`
- Canonical firmware version is stored in `firmware/esp32c3_bridge/VERSION`
