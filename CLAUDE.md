# CLAUDE.md — Claude Code Session Reference

> **This file is owned and maintained by Claude Code.**
> Do NOT edit AGENTS.md or README.md to match this file — only this file is updated.
> Source of truth for project rules: `AGENTS.md` and `README.md`.

---

## Branch Context — `release/0.2.0`

- **Managed by**: Claude Code (this branch)
- **Parallel branch**: Managed by Codex under a separate branch
- **Objective**: Allow Eyal to progress with either Claude or Codex independently, then compare progress and code quality between the two AI agents.
- **Rule**: Changes on this branch are authored and committed by Claude. Do not merge Codex-branch changes here without Eyal's explicit review and decision.

---

## SESSION START PROTOCOL (MANDATORY)

At the start of every new coding session with a fresh context, Claude must:

1. Read `AGENTS.md` (project rules).
2. Read `README.md` (architecture, run, and workspace rules).
3. Compare both files against this `CLAUDE.md`.
4. Update this `CLAUDE.md` to reflect the current state of both files.
5. **Provide a textual GAP assessment** (see below) if any rule, instruction, or section in `AGENTS.md` or `README.md` is not covered by — or conflicts with — this `CLAUDE.md`.

### GAP Assessment Format

When a gap is detected, report it inline at session start using this format:

```
GAP ASSESSMENT — <date>
Source: <AGENTS.md | README.md>
Section: <section name or rule number>
Gap: <description of what is missing or out of date in CLAUDE.md>
Action: <updated | added | flagged for user review>
```

If no gaps are found, confirm: `GAP ASSESSMENT: CLAUDE.md is in sync with AGENTS.md and README.md.`

---

## Project Identity

- **Project**: Eyal Espresso Server Simulator
- **Stack**: Python / FastAPI backend, browser UI, optional ESP32-C3 bridge firmware
- **Role**: Host-side simulator for the Eyal Espresso transport and controller link
- **Companion project**: `Eyal_espresso_client` (separate repository, same workspace)

---

## Rules from AGENTS.md

### Rule 1 — Verification Commit Prompt
After every accepted successful local verification cycle, ask whether to commit current changes and create a sub-version release.

### Rule 2 — Versioning Scheme
- Version format: `X.Y.Z` stored in the `VERSION` file.
- `X` (major): Functionality additions/removals and refactoring-level changes.
- `Y` (minor): Bug fixes and smaller functionality changes.
- `Z` (patch/sub-version): Increment on every accepted verification cycle.

### Rule 3 — Function and Header Documentation
Every function declaration and definition must have a short header comment block including:
- `@brief` — purpose
- `@details` — implementation notes
- Parameters and return value where applicable

### Rule 4 — Revision Document
Maintain `docs/REVISION_HISTORY.doc` with:
- Sections grouped by major/minor revisions (`X.Y`)
- A short change summary per entry
- A continuously maintained "Latest Version Feature List" section

### Rule 5 — Rule-Gated Verification/Build/Flash Sequence
For any verification/build/flash request, execute this gated sequence and report each gate completion:
- Gate 1: quick compliance check of `AGENTS.md`, `README.md`, and `CLAUDE.md`.
- Gate 2: run `scripts/start_wait_sound.ps1`.
- Gate 3: execute requested steps sequentially only (never parallel flash).
- Gate 4: run `scripts/stop_wait_sound.ps1`.
- Gate 5: on success, run `scripts/play_build_success_sound.ps1`.
- Post-flash monitor capture is optional and only required when explicitly requested.
- If any gate fails, stop immediately and report: `RULE-GATED SEQUENCE BROKEN: <gate>`.

---

## Rules from README.md

### Architecture — Source Files
| File | Role |
|---|---|
| `server/app.py` | FastAPI entry point; `/health`, `/api/app-info`, API router, serves UI |
| `server/api/routes.py` | HTTP API surface: config, COM-port, reset/init/keepalive/send-data, snapshots |
| `server/sim/link_state_machine.py` | **Active** simulator runtime: workflow, counters, watchdog, transition logging, snapshot |
| `server/transport/serial_link.py` | Single-owner serial manager: open/close COM, RX loop, framing, counters, force-release |
| `ServerInterface/frame_codec.py` | Shared frame codec — protocol authority for host-side frame encoding/decoding |
| `firmware/esp32c3_bridge/main/bridge_main.c` | Optional bridge firmware: Wi-Fi AP + TCP listener for the ESP32-S3 client |

> **Note:** `server/sim/controller_state.py` is an older/alternate path and is **not** the active runtime. The live API imports `link_runtime` from `server/sim/link_state_machine.py`.

### End-to-End Topology
1. Browser UI → FastAPI (HTTP)
2. FastAPI routes → in-process simulator runtime
3. Simulator runtime → serial manager
4. Serial manager → ESP32-C3 bridge (COM port)
5. ESP32-C3 bridge → Wi-Fi AP `EyalSimulatorAP` + TCP port `3333`
6. ESP32-S3 client → joins AP and connects over TCP using framed transport

The Python process does **not** directly own the Wi-Fi/TCP listener — the bridge firmware does.

### Transport Model
- Shared frame codec with CRC16-CCITT
- Frame start bytes: `0xA5 0x5A`
- Message family: `RESET`, `INITIALIZE`, `CONNECT`, `DISCONNECT`, `KEEPALIVE`, `ERROR`, `ACK`, `DATA`
- Mirrored counters: server live integer, client live integer, sequence
- Runtime states: `reset → initialize → connect → keepalive_server_send ↔ keepalive_client_return → error`
- Keepalive contract: server-initiated; bridge sends authoritative even `ServerLiveInteger`, client returns odd `ClientLiveInteger`
- Retry escalation: timeout-driven, three attempts
- Telemetry: `Transport Last Delay [mS]`, `Transport Max Delay [mS]`, `Total Errors` — firmware-authored on ESP32-C3, mirrored by simulator UI; `-1` last-delay outside keepalive states

### API Surface (`server/api/routes.py`)
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

### Repository Layout
- `server/` — FastAPI app, API routes, runtime state machines, transport code
- `server/ui/` — browser UI assets
- `ServerInterface/` — shared protocol/codec
- `firmware/esp32c3_bridge/` — ESP-IDF bridge firmware
- `tests/` — simulator API tests
- `docs/` — requirements, revision history, transport architecture docs
- `scripts/` — launch, verification, sound, and documentation helpers
- `VERSION` — simulator app version
- `firmware/esp32c3_bridge/VERSION` — bridge firmware version

### Run / Launch
Install dependencies:
```bash
pip install -r requirements.txt
```
Launch simulator (mandatory method):
```bash
powershell.exe -NoProfile -ExecutionPolicy Bypass -File C:\Espressif\Eyal_Projects_ESP32_S3\Eyal_espresso_server_simulator\scripts\launch_simulator_ui.ps1
```
Launcher always runs requirements sync first before backend startup.

### Claude Code — Run Simulator ("run sim")
When Eyal says **"run sim"**, Claude must launch `launch_simulator_ui.ps1` asynchronously — fire and forget, do not wait for it to close. The simulator runs as a long-lived background process alongside the session.

**Confirmed working method from Claude Code's shell:**
```bash
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "Start-Process powershell.exe -ArgumentList '-NoProfile -ExecutionPolicy Bypass -File C:\Espressif\Eyal_Projects_ESP32_S3\Eyal_espresso_server_simulator\scripts\launch_simulator_ui.ps1'"
```

Key points:
- No `-Wait` — the script is intentionally launched async and left running.
- If startup fails, read the terminal block first (`[Simulator Launch Prerequisite Failure]`, `[Simulator Launch Verification Failure]`, or `[Simulator Launch Runtime Failure]`) — terminal output is the source of truth, not the popup.

### Session Setup — Every Session
1. Open terminal in `Eyal_espresso_server_simulator`.
2. Launch via `launch_simulator_ui.ps1` (see above).
3. If startup fails, read the terminal block first (`[Simulator Launch Prerequisite Failure]`, `[Simulator Launch Verification Failure]`, or `[Simulator Launch Runtime Failure]`) — terminal is the source of truth, not the popup.
4. Keep VS Code interpreter pinned to:
   `C:\Espressif\Eyal_Projects_ESP32_S3\Eyal_espresso_server_simulator\.venv\Scripts\python.exe`

Helper scripts:
- `scripts/run_simulator.bat` — wrapper that calls `launch_simulator_ui.ps1`
- `scripts/run_simulator.ps1` — backend-only launcher without UI-open flow

### Bridge Firmware Build and Flash (MANDATORY METHOD)
```bash
cmd.exe /c C:/Espressif/Eyal_Projects_ESP32_S3/Eyal_espresso_server_simulator/scripts/idfw.cmd -p <PORT> build flash
```
- This is the ONLY approved flash method for all sessions (including Codex/WSL).
- Do not use `scripts/flash_hidden.ps1`.
- Do not use `scripts/idfw.ps1`.
- Do not use direct `idf.py` commands.
- Do not use PowerShell `Start-Process` wrappers for flashing.

### Sound Cues
Use the sound cue scripts in `scripts/` as workflow notifications for verification/build/flash outcomes.

### Rule-Gated Execution Sequence
For every verification/build/flash task, use the exact gated sequence defined in `AGENTS.md` and `README.md`:
compliance check, `start_wait_sound`, sequential execution, `stop_wait_sound`, and success sound.

### Git Commits
Perform all git commits with real git access (not sandboxed). Reference format:
```bash
git add <file>
git -c user.name="Codex" -c user.email="codex@local" commit -m "docs: <message>"
```

### Workspace Review Rules
- Read all files in both workspace projects: `Eyal_espresso_client` and `Eyal_espresso_server_simulator`.
- Review both project architectures before making code changes.
- Read both `README.md` files and follow rules listed in them.
- Verify git is active before making or finalizing changes.

### Session Start Approval Bootstrap (Codex sessions)
Before substantial work, request saved prefix approvals for:
- `code --reuse-window --goto <WindowsPath:line:col>`
- `powershell.exe -NoProfile -ExecutionPolicy Bypass -File C:\Espressif\...\scripts\start_wait_sound.ps1`
- `powershell.exe -NoProfile -ExecutionPolicy Bypass -File C:\Espressif\...\scripts\stop_wait_sound.ps1`
- `powershell.exe -NoProfile -ExecutionPolicy Bypass -File C:\Espressif\...\scripts\play_wait_sound.ps1`
- `powershell.exe -NoProfile -ExecutionPolicy Bypass -File C:\Espressif\...\scripts\play_build_success_sound.ps1`
- Common build/flash wrappers under `C:\Espressif\...\scripts\`
- Any `.exe` file that only creates or modifies files within `C:\Espressif`

---

## Current Architectural Intent
- The server simulator is transport-first, not yet a full espresso-machine domain simulator.
- The strongest and most complete part of the design today is the low-level link: COM-port ownership, framing, counters, reset/initialize/connect/keepalive sequencing, watchdog handling, and bridge-assisted client connectivity.
- Higher-level machine behavior exists mainly as scaffolding compared with the transport layer.

## Architecture Notes
- The simulator is transport-first; higher-level machine behavior is scaffolding compared to the transport layer.
- Strongest parts: COM-port ownership, framing, counters, reset/initialize/connect/keepalive sequencing, watchdog handling, bridge-assisted connectivity.
- The server does not own the Wi-Fi/TCP listener directly — the ESP32-C3 bridge firmware does.
- `server/sim/link_state_machine.py` is the current source of truth for active runtime behavior.
- Server owns: mirrored transport behavior, simulator controls, host-side serial ownership.
- Client owns: operator UI, Wi-Fi/TCP client transport.
- Both share the same message vocabulary and watchdog assumptions.

---

*Last synced: 2026-03-21 — Updated to include README run-sim and build-verification sections; commit template aligned.*
