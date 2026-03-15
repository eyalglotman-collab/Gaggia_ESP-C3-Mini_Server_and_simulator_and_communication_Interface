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
  - `keepalive`
  - `wait_for_com_reset`

The simulator keeps transport logs and returns snapshot-shaped data to the UI instead of exposing mutable runtime internals directly.

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

Install dependencies first:

```bash
pip install -r requirements.txt
```

Then run the backend with your preferred launcher. The repository already includes Windows helpers such as:

- `scripts/run_simulator.bat`
- `scripts/run_simulator.ps1`
- `scripts/launch_simulator_ui.ps1`

## Current Architectural Intent

The server simulator is transport-first, not yet a full espresso-machine domain simulator. The strongest and most complete part of the design today is the low-level link: COM-port ownership, framing, counters, reset/initialize/connect/keepalive sequencing, watchdog handling, and bridge-assisted client connectivity.

Higher-level machine behavior exists mainly as scaffolding compared with the transport layer.

All git commits for this repository should be performed directly with real git access, not through the sandboxed environment.

Reference direct-commit method used here:

```bash
git add README.md
git -c user.name="Codex" -c user.email="codex@local" commit -m "docs: <message>"
```

Codex has permission to run any `.exe` file it chooses, as long as that executable only creates or modifies files within `C:\Espressif`.
