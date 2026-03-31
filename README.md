# Eyal Espresso Server Simulator

Host-side simulator for the Eyal Espresso transport and controller link. This project provides the server half of the system, a browser-based operator UI, and an optional ESP32-C3 bridge firmware for serial-to-TCP integration.

## Purpose

The simulator exists to let the client firmware connect to a controllable server-side environment during transport and integration work. It owns:

- A FastAPI backend for simulator control and status snapshots.
- A browser UI served by the backend.
- A single-owner serial transport manager for the connected COM port.
- A low-level link state machine that mirrors the client transport contract.
- Optional ESP32-C3 bridge firmware that turns USB serial control into a Wi-Fi AP plus TCP listener for the ESP32-S3 client.

## Verified Architecture

The active runtime path in code is:

1. `server/app.py`
   FastAPI entry point. It exposes `/health`, `/api/app-info`, mounts the API router, and serves `server/ui/index.html`.
2. `server/api/routes.py`
   HTTP API surface for transport configuration, COM-port ownership, reset/initialize/keepalive/send-data actions, and link snapshots.
3. `server/sim/link_state_machine.py`
   The active simulator runtime. It owns the low-level workflow, mirrored counters, watchdog logic, transition logging, and the state snapshot returned to the UI.
4. `server/transport/serial_link.py`
   Single-owner serial manager. It opens/closes the COM port, runs the background RX loop, frames traffic, keeps transport counters, and can force-release likely external COM-port holders.
5. `ServerInterface/frame_codec.py`
   Shared frame codec used by the Python runtime. This is the protocol authority for frame encoding/decoding on the host side.
6. `firmware/esp32c3_bridge/main/bridge_main.c`
   Optional bridge firmware. It accepts framed commands from the host over USB serial and exposes the Wi-Fi AP / TCP listener used by the ESP32-S3 client.

## Important Architecture Clarification

`server/sim/controller_state.py` appears to be an older or alternate simulation path and is not the active transport runtime used by `server/app.py`. The live API imports `link_runtime` from `server/sim/link_state_machine.py`, so that file is the current source of truth for the running server architecture.

## End-to-End Topology

The current system is structured like this:

1. Browser UI talks to FastAPI over HTTP.
2. FastAPI routes call the in-process simulator runtime.
3. The simulator runtime talks to the serial manager.
4. The serial manager talks over one COM port to the ESP32-C3 bridge.
5. The ESP32-C3 bridge exposes Wi-Fi AP `EyalSimulatorAP` and TCP port `3333`.
6. The ESP32-S3 client joins that AP and connects to the bridge over TCP using the framed transport.

This means the Python process does not directly own the Wi-Fi/TCP listener used by the client. The bridge firmware owns that low-level endpoint.

## Transport Model

The simulator mirrors the client’s framed protocol and low-level states.

- Shared frame codec with CRC16-CCITT
- Shared start-of-frame bytes: `0xA5 0x5A`
- Shared message family: `RESET`, `INITIALIZE`, `CONNECT`, `DISCONNECT`, `KEEPALIVE`, `ERROR`, `ACK`, `DATA`
- Mirrored counters:
  - server live integer
  - client live integer
  - sequence
- Main runtime states:
  - `reset`
  - `initialize`
  - `connect`
  - `keepalive_server_send`
  - `keepalive_client_return`
  - `error`

The simulator keeps transport logs and returns snapshot-shaped data to the UI instead of exposing mutable runtime internals directly.

The keepalive contract is server-initiated: bridge firmware sends one authoritative even `ServerLiveInteger`, the client returns one odd `ClientLiveInteger`, and retry escalation is timeout-driven with three attempts.

Telemetry for `Transport Last Delay [mS]`, `Transport Max Delay [mS]`, and `Total Errors` is firmware-authored on the ESP32-C3 and mirrored by the simulator UI, including `-1` last-delay markers outside keepalive states.

## API Surface

Key routes currently implemented in `server/api/routes.py`:

- `GET /health`
- `GET /api/app-info`
- `GET /api/link`
- `POST /api/config`
- `POST /api/transport/open`
- `POST /api/transport/close`
- `POST /api/transport/release-com`
- `POST /api/transport/toggle-wifi`
- `POST /api/command/reset`
- `POST /api/command/initialize`
- `POST /api/command/keepalive`
- `POST /api/command/send-data`
- `POST /api/simulation/config`
- `POST /api/telemetry/reset-total-errors`
- `POST /api/telemetry/reset-max-delay`

## Repository Layout

- `server/`: FastAPI app, API routes, runtime state machines, and transport code
- `server/ui/`: browser UI assets
- `ServerInterface/`: shared protocol/codec implementation
- `firmware/esp32c3_bridge/`: ESP-IDF bridge firmware
- `tests/`: simulator API tests
- `docs/`: requirements, revision history, and transport architecture docs
- `scripts/`: launch, verification, sound, and documentation helpers
- `VERSION`: simulator app version
- `firmware/esp32c3_bridge/VERSION`: bridge firmware version


## Design Document Preservation Rule

When editing design documents, preserve existing chapters by default.

- Keep all chapters and sections unless the design in that chapter is no longer relevant.
- If a chapter is removed or substantially replaced, explicitly document why it became irrelevant.
- Prefer additive updates (new chapters/subsections) over destructive rewrites.
- Keep the generated source script and generated `.docx` in sync so regeneration preserves the same chapter set.


## Run

You must install dependencies first:

```bash
pip install -r requirements.txt
```

You must launch the simulator through `scripts/launch_simulator_ui.ps1`.

### Claude Code — Run Simulator ("run sim")

When Eyal says **"run sim"**, Claude must launch `launch_simulator_ui.ps1` asynchronously — fire and forget, do not wait for it to close. The simulator runs as a long-lived background process alongside the session.

**Confirmed working method from Claude Code's shell:**
```bash
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "Start-Process powershell.exe -ArgumentList '-NoProfile -ExecutionPolicy Bypass -File C:\Espressif\Eyal_Projects_ESP32_S3\Eyal_espresso_server_simulator\scripts\launch_simulator_ui.ps1'"
```

Key points:
- No `-Wait` — the script is intentionally launched async and left running.
- If startup fails, read the terminal block first (`[Simulator Launch Prerequisite Failure]`, `[Simulator Launch Verification Failure]`, or `[Simulator Launch Runtime Failure]`) — terminal output is the source of truth, not the popup.

## Session Setup And Recovery (Use Every Session)

To avoid repeated missing-package or false-missing popups, use this session startup sequence:

Use this every session:

- Run `launch_simulator_ui.ps1` from the server repo.
- If it fails, read terminal block first (`Simulator Launch ... Failure`).
- Keep VS Code interpreter pinned to `C:\Espressif\Eyal_Projects_ESP32_S3\Eyal_espresso_server_simulator\.venv\Scripts\python.exe`.

1. Open a terminal in `Eyal_espresso_server_simulator`.
2. Launch only with:

```bash
powershell.exe -NoProfile -ExecutionPolicy Bypass -File C:\Espressif\Eyal_Projects_ESP32_S3\Eyal_espresso_server_simulator\scripts\launch_simulator_ui.ps1
```

3. The launcher now always runs requirements sync first (`pip install -r requirements.txt`) before backend startup.
4. If startup fails, read the terminal block first (`[Simulator Launch Prerequisite Failure]`, `[Simulator Launch Verification Failure]`, or `[Simulator Launch Runtime Failure]`).
5. The popup mirrors the same failure list for convenience, but the terminal output is the source of truth for copy/paste diagnostics.
6. In VS Code, keep the interpreter fixed to:
   `C:\Espressif\Eyal_Projects_ESP32_S3\Eyal_espresso_server_simulator\.venv\Scripts\python.exe`

This process is required to prevent repeated two-hour recovery loops caused by mismatched interpreters or stale package environments.

The repository also contains related helper scripts:

- `scripts/run_simulator.bat` (wrapper that calls `scripts/launch_simulator_ui.ps1`)
- `scripts/run_simulator.ps1` (backend-only launcher without the UI-open prompt flow)

### Bridge Firmware Build and Flash

This is the ONLY approved bridge flash method:

```bash
cmd.exe /c C:/Espressif/Eyal_Projects_ESP32_S3/Eyal_espresso_server_simulator/scripts/idfw.cmd -p <PORT> build flash
```

Mandatory rules:

- Use exactly the command above for every bridge flash task.
- Do not split flashing into separate commands.
- Do not use `scripts/flash_hidden.ps1`.
- Do not use `scripts/idfw.ps1`.
- Do not use direct `idf.py` commands.
- Do not use PowerShell `Start-Process` wrappers for flashing.

## Rule-Gated Execution Sequence (Mandatory)

For every verification/build/flash task, Codex/Claude must use this exact gated sequence and explicitly report each gate:

1. Gate 1: quick compliance check of `AGENTS.md`, `README.md`, and `CLAUDE.md`.
2. Gate 2: run `scripts/start_wait_sound.ps1`.
3. Gate 3: execute the single approved flash command above (no alternatives and no parallel flashing).
4. Gate 4: run `scripts/stop_wait_sound.ps1`.
5. Gate 5: on successful completion, run `scripts/play_build_success_sound.ps1`.

Post-flash monitor capture is optional and only required when explicitly requested.

If any gate fails, the sequence is non-compliant and execution must stop immediately with:
`RULE-GATED SEQUENCE BROKEN: <gate>`.

### Flash Enforcement Note

If flashing fails, retry using the same approved command only. Do not switch to an alternative flash path.

## Current Architectural Intent

The server simulator is transport-first, not yet a full espresso-machine domain simulator. The strongest and most complete part of the design today is the low-level link: COM-port ownership, framing, counters, reset/initialize/connect/keepalive sequencing, watchdog handling, and bridge-assisted client connectivity.

Higher-level machine behavior exists mainly as scaffolding compared with the transport layer.

You must use the sound-related scripts in `scripts/` as workflow notifications for verification/build/flash outcomes.
You must perform all git commits for this repository directly with real git access, not through the sandboxed environment.

You must use this direct-commit method as the reference:

```bash
git add README.md
git -c user.name="Codex" -c user.email="codex@local" commit -m "docs: <message>"
```

### GitHub Repository Ownership Rule

- All commits and version updates for this project must be performed in this repository and pushed to:
  - `git@github.com:eyalglotman-collab/Gaggia_ESP-C3-Mini_Server_and_simulator_and_communication_Interface.git`
- Do not perform official versioning or release commits in temporary/mirror copies.
- `VERSION` and `firmware/esp32c3_bridge/VERSION` updates, plus release tags, must match the commit history of this GitHub repository.

## Workspace Review Rules

- You must read all files in the two workspace projects: `Eyal_espresso_client` and `Eyal_espresso_server_simulator`.
- You must review both project architectures and be prepared to make code changes.
- You must read both `README.md` files and follow the rules listed in them.
- You must verify that git is active before making or finalizing changes.

## Session Start Approval Bootstrap

- At the beginning of every new Codex session, before substantial work, Codex must run a pre-approval bootstrap and request saved prefix approvals for common commands.
- Codex must ask for these prefix types first so future commands do not repeatedly prompt:
  - `code --reuse-window --goto <WindowsPath:line:col>`
  - `powershell.exe -NoProfile -ExecutionPolicy Bypass -File C:\Espressif\...\scripts\start_wait_sound.ps1`
  - `powershell.exe -NoProfile -ExecutionPolicy Bypass -File C:\Espressif\...\scripts\stop_wait_sound.ps1`
  - `powershell.exe -NoProfile -ExecutionPolicy Bypass -File C:\Espressif\...\scripts\play_wait_sound.ps1`
  - `powershell.exe -NoProfile -ExecutionPolicy Bypass -File C:\Espressif\...\scripts\play_build_success_sound.ps1`
  - Common build/flash wrappers under `C:\Espressif\...\scripts\` that this workspace uses.
- During this bootstrap, Codex must explicitly ask the user to save/remember the prefix rule when the runtime approval UI appears.

Executable approvals must stay scoped to known workspace tools under `C:\Espressif\Eyal_Projects_ESP32_S3\...\scripts\` (or equivalent project paths under `C:\Espressif`). Do not grant blanket approval for arbitrary `.exe` binaries.
