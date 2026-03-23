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

You must build and flash bridge firmware through `cmd.exe` with an absolute forward-slash path.

```bash
cmd.exe /c C:/Espressif/Eyal_Projects_ESP32_S3/Eyal_espresso_server_simulator/scripts/idfw.cmd build
cmd.exe /c C:/Espressif/Eyal_Projects_ESP32_S3/Eyal_espresso_server_simulator/scripts/idfw.cmd -p <PORT> flash
```

You must run build and flash sequentially, with `build` first and `flash` second.
For Codex/WSL sessions, this absolute-path `cmd.exe` method is the required build and flash path.

## Rule-Gated Execution Sequence (Mandatory)

For every verification/build/flash task, Codex/Claude must use this exact gated sequence and explicitly report each gate:

1. Gate 1: quick compliance check of `AGENTS.md`, `README.md`, and `CLAUDE.md`.
2. Gate 2: run `scripts/start_wait_sound.ps1`.
3. Gate 3: execute requested build/flash actions sequentially only (no parallel flashing).
4. Gate 4: run post-flash monitor capture for each flashed target and summarize results.
5. Gate 5: run `scripts/stop_wait_sound.ps1`.
6. Gate 6: on successful completion, run `scripts/play_build_success_sound.ps1`.

If any gate fails, the sequence is non-compliant and execution must stop immediately with:
`RULE-GATED SEQUENCE BROKEN: <gate>`.

### Claude Code Build Verification (non-interactive shell limitation)

When running inside Claude Code's bash shell, Windows console programs (`idf.py`, `ninja`) write output
to the Windows console buffer rather than the pipe, so no build output is visible and output capture
via `2>&1` or PowerShell redirects does not work. If the binary timestamp does not update after running
the build command, the build did not reach ninja.

**Confirmed working method from Claude Code's shell** (clears MSYSTEM, uses -NoNewWindow to pipe output):

Build:
```bash
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "Start-Process cmd.exe -ArgumentList '/c set MSYSTEM=& C:\Espressif\Eyal_Projects_ESP32_S3\Eyal_espresso_server_simulator\scripts\idfw.cmd build' -Wait -NoNewWindow -PassThru"
```

Flash:
```bash
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "Start-Process cmd.exe -ArgumentList '/c set MSYSTEM=& C:\Espressif\Eyal_Projects_ESP32_S3\Eyal_espresso_server_simulator\scripts\idfw.cmd -p COM4 flash' -Wait -NoNewWindow -PassThru"
```

If these fail, ask Eyal to run `idfw.cmd build` or `idfw.cmd -p COM4 flash` from a real CMD or VS Code integrated terminal and report back.

Otherwise verify the build result using these checks instead of looking at idf.py output:

**1. Check the binary exists and has a recent timestamp:**
```bash
ls -la .idfbuild/esp32c3_bridge/eyal_espresso_c3_bridge.bin
```

**2. Confirm no source changes since the last known-good build:**
```bash
git diff <last-good-commit> HEAD -- firmware/esp32c3_bridge/main/
```
If the diff is empty, the existing binary in `.idfbuild/esp32c3_bridge/` is valid and up to date.

**3. Play the build success sound after confirming a valid binary:**
```bash
powershell.exe -NoProfile -ExecutionPolicy Bypass -File C:\Espressif\Eyal_Projects_ESP32_S3\Eyal_espresso_server_simulator\scripts\play_build_success_sound.ps1
```

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

You must allow Codex to run any `.exe` file it chooses, as long as that executable only creates or modifies files within `C:\Espressif`.
