# Transport Contract Baseline

## Purpose

This file is the canonical machine-readable design baseline for low-level transport behavior in the simulator repository. The `.docx` design documents must match this file and the Mermaid diagrams in the same folder.

## Maintenance Rules

- Update this file together with any `.docx` change that affects workflow, state machines, packets, watchdogs, timing, ownership, or failure handling.
- Use exact code-facing names for states, packet types, counters, modules, and events.
- Keep Mermaid diagrams and the tables in this file consistent with each other.
- Do not leave important behavior only in screenshots or rendered images.

## Ownership Summary

| Item | Owner | Notes |
| --- | --- | --- |
| `HostLiveInteger` | PC simulator host | Must advance every keep-alive period to prove host forward progress. |
| `DeviceLiveInteger` | ESP32-C3 transport controller | Returned to let the host detect bridge-side stalls independently. |
| CRC validation | Low-level transport layer | FastAPI and high-level simulator logic should not re-implement integrity checks. |
| Watchdog enforcement | Low-level transport layer on both sides | Missing forward progress forces transition to `error`. |
| Recovery decision | Supervisory host logic | Only explicit `reset` recovers from `error`. |

## Shared Interface Boundary

| Item | Required Location | Notes |
| --- | --- | --- |
| Portable frame definitions | `ServerInterface/` | Packet enums, binary framing, and CRC rules must live in the shared interface layer before platform adapters consume them. |
| Native MCU-facing API | `ServerInterface/native/include/server_interface/c_api.h` | The portable C-facing API is the intended integration boundary for future STM32 firmware. |
| PC server compatibility path | `server/transport/frame_codec.py` | Existing Python code may use a compatibility shim, but the canonical protocol rules should originate in `ServerInterface/`. |
| Platform-specific transport ownership | `server/transport/serial_link.py` or MCU HAL adapter | Serial, USB, sockets, and host runtime ownership remain outside the shared interface core. |

## Mirrored Transport Configuration

| Field | Default | Purpose |
| --- | --- | --- |
| `serial_port` | `COM4` | USB serial endpoint from the PC host into the ESP32-C3 bridge. |
| `wifi_ssid` | `EyalSimulatorAP` | Bridge-side Wi-Fi network identifier mirrored from the client contract. |
| `wifi_password` | `espresso1234` | Bridge-side Wi-Fi credential mirrored from the client contract. |
| `server_ip` | `192.168.4.1` | Default bridge-side/server endpoint address used during connect. |
| `server_port` | `3333` | TCP endpoint used for the low-level transport session. |
| `wifi_connect_timeout_ms` | `10000` | Bound on low-level Wi-Fi association/visibility preparation. |
| `tcp_connect_timeout_ms` | `3000` | Bound on TCP session establishment after Wi-Fi is ready. |
| `keepalive_period_ms` | `100` | Host-driven keep-alive cadence. |

## Backend Launch Contract

### Purpose

This section defines the required local runtime-launch behavior for the PC-hosted simulator backend and browser UI. It exists to keep development automation and operator testing deterministic on this Windows host.

### Launch Rules

| Item | Required Practice | Notes |
| --- | --- | --- |
| Backend host | Use a deterministic repo-local launcher | The preferred launcher is `scripts\run_simulator.ps1`, which resolves the repository root and starts `uvicorn` from the local `.venv` when available. |
| Python runtime | Prefer the repository virtual environment | Use `.\.venv\Scripts\python.exe` before falling back to a global `python`. |
| Process lifetime | Run the backend under a stable supervising host | The backend must not rely on a transient editor task or short-lived tool shell to remain alive. |
| Logging | Preserve stdout/stderr for diagnosis | Launches should keep console visibility or redirect output into repo-local logs. |
| Readiness check | Verify runtime readiness before opening the UI | A healthy process alone is insufficient; require a successful `GET /health` response. |
| Browser launch | Open the UI only after readiness is confirmed | Do not open the browser optimistically. |
| Frontend freshness | Bypass stale browser state | Use a cache-busting URL or equivalent fresh-load mechanism so the browser does not reuse stale assets. |
| Separation of concerns | Treat launcher reliability separately from app correctness | If `uvicorn` runs in the foreground but a detached host dies, the issue is process-hosting automation, not necessarily an application bug. |

### Best-Practice Rationale

- use a deterministic launcher script or service wrapper
- capture stdout and stderr to logs or keep them visible in the supervising console
- require a concrete readiness signal such as `/health`
- supervise the process with a stable host if it must outlive the initiating shell
- separate application correctness from editor, sandbox, or task-runner lifetime

### Recommended Local Launch Flow

| Step | Action | Success Signal | Failure Interpretation |
| --- | --- | --- | --- |
| 1 | Start the backend with `scripts\run_simulator.ps1` or an equivalent repo-local wrapper | A persistent `uvicorn` host process exists | Launcher or environment problem |
| 2 | Wait for `GET /health` to return `200 OK` with `{"status":"ok"}` | Backend is ready to serve API and UI | Backend startup, dependency, or host-lifetime problem |
| 3 | Open `http://127.0.0.1:8000/` with a cache-busting query string | Browser loads current simulator UI | Browser cache or frontend delivery issue |
| 4 | Use runtime logger and monitor surfaces during testing | Operator can inspect actionable events | Observability gap in launch or UI |

## State Definitions

| State | Purpose | Entry Actions | Exit Conditions |
| --- | --- | --- | --- |
| `reset` | Stop transmission, clear buffers, and re-initialize the ESP controller. | Clear counters, buffers, stale link ownership, and active supervision before issuing `RESET`. | Reset complete and parameters available for initialize. |
| `initialize` | Load the ESP controller with mirrored transport constants. | Validate mirrored COM/Wi-Fi/TCP configuration, send Wi-Fi/server constants, then automatically issue `CONNECT`. | Automatic hand-off into `connect`, or immediate fault occurs. |
| `connect` | Wait for the ESP controller to acknowledge the active connection. | Hold the mirrored Wi-Fi-ready/connect-attempt context while waiting for `connect_ack` or equivalent progress. | Connect response succeeds and promotes to `keepalive`, or timeout/fault occurs. |
| `keepalive` | Supervise the active low-level connection. | Exchange liveness traffic, advance `HostLiveInteger`, and watch for missed responses. | Data traffic is enabled or keepalive supervision fails. |
| `send_data` | Allow application payload traffic on the active low-level connection. | Send validated `DATA` frames while the keepalive path remains healthy. | Operator resumes keepalive focus or a supervision fault occurs. |
| `error` | Latch low-level fault and block normal traffic. | Preserve error reason and last counters, stop forwarding payloads. | Explicit `reset` command only. |

## Transition Table

| Current State | Trigger | Guard / Condition | Action | Next State | Timeout / Failure Behavior |
| --- | --- | --- | --- | --- | --- |
| `reset` | Reset command | Operator requests hard recovery or fresh start | Stop transmission, clear buffers, and issue `RESET` to the ESP controller | `reset` | Reset-send failure moves to `error`. |
| `reset` | Initialize command | COM is available and mirrored Wi-Fi/TCP configuration is coherent | Send transport constants and automatically issue `CONNECT` | `connect` | Validation, COM availability, or bridge bring-up failure moves to `error`. |
| `connect` | Connect response received | `connect_ack`, `KEEPALIVE`, or `DATA` progress arrives in time | Enable keepalive supervision and send-data path | `keepalive` | Connect timeout or malformed response moves to `error`. |
| `keepalive` | Send Data command | Connect success already enabled payload traffic | Send `DATA` frame while supervision remains active | `send_data` | Payload attempt before connect success moves to `error`. |
| `keepalive` | Fault detected | Keepalive timeout, watchdog timeout, malformed frame, transport loss | Latch fault and stop forwarding | `error` | Fault is terminal until explicit reset. |
| `send_data` | Keepalive command | Operator resumes explicit liveness supervision | Send `KEEPALIVE` and continue supervising the active session | `keepalive` | Missing keepalive response moves to `error`. |
| `send_data` | Fault detected | Keepalive timeout, watchdog timeout, malformed frame, transport loss | Latch fault and stop forwarding | `error` | Fault is terminal until explicit reset. |
| `error` | Recovery command | Explicit hard recovery | Clear fault and restart stack | `reset` | No implicit recovery allowed. |

## Packet Definitions

| Packet | Purpose | Sender | Receiver | Required Fields | Normal Response | Timeout Rule | Error Handling |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `RESET` | Force hard reset and self-test. | Supervisory host | Low-level peer | `protocol_version`, `message_type`, reset profile/parameters, CRC | `RESET_ACK` | Supervisor expects bounded response time from reset path. | Failure enters `error`. |
| `INITIALIZE` | Prepare low-level resources. | Supervisory host | Low-level peer | serial port, Wi-Fi SSID/password, server IP/port, watchdog settings, CRC | `INITIALIZE_ACK` | Initialization must complete before connect window expires. | Validation failure enters `error`. |
| `CONNECT` | Enter active session. | Supervisory host | Low-level peer | connection role or endpoint reference, CRC | `CONNECT_ACK` | Session establishment timeout enters `error`. | Wi-Fi/TCP socket failure enters `error`. |
| `KEEPALIVE` | Prove host forward progress. | PC simulator host | Low-level peer | incremented `HostLiveInteger`, sequence, CRC | `KEEPALIVE_ACK` with `DeviceLiveInteger` and status | Every 100 mSec. | Missing progress enters `error`. |
| `DATA` | Carry application payload after validation. | Either side | Peer | payload, sequence, CRC | `ACK` or application response | Normal transport timeout policy applies. | Invalid frame is rejected before upper layer sees payload. |
| `ERROR` | Report latched low-level fault. | Faulting side | Supervisory peer | error code, state, last counters, summary, CRC | Recovery command | Immediate supervisory review required. | Link remains in `error`. |

## Timing Rules

- Keep-alive cadence is `100 mSec`.
- The PC simulator host must advance `HostLiveInteger` every keep-alive period.
- The ESP32-C3 transport controller should return `DeviceLiveInteger` so liveness is observable in both directions.
- If expected liveness progress is not observed in time, the receiver must transition to `error`.

## Failure Modes

| Failure Mode | Detection Point | Required Action | Allowed Recovery |
| --- | --- | --- | --- |
| CRC failure | Low-level frame parser | Drop frame and latch fault | `reset` |
| Host watchdog failure | ESP32-C3 transport controller | Assume host stalled and latch fault | `reset` after host recovers |
| Device watchdog failure | PC simulator host | Stop trusting link and latch fault | `reset` |
| USB COM loss | Host or bridge | Stop transport and latch fault | `reset` after COM recovery |
| COM port not found | Simulator host open/initialize path | Latch explicit COM availability fault before bridge initialization continues | `reset` after COM recovery |
| Wi-Fi association failure | Bridge-side initialize/connect | Latch fault with Wi-Fi status | `reset` |
| Configured AP offline / not visible | Bridge-side initialize/connect or mirrored simulator validation | Latch explicit AP-not-visible fault before claiming Wi-Fi-ready state | `reset` after RF or configuration changes |
| TCP server not found / not listening | Bridge-side connect | Latch explicit server/listener availability fault | corrected endpoint plus `reset` |
| Keepalive response timeout | Keepalive supervision | Latch explicit keepalive fault and stop progression | `reset` |
| Generic unknown transport failure | Any stage without stronger evidence | Latch stage-specific fault and stop progressing state | `reset` |
| TCP session loss | Bridge-side keepalive or send-data state | Latch fault and stop forwarding | `reset` |
| Malformed packet / unsupported version | Parser | Reject packet and latch fault | `reset` after protocol correction |

## Simulator UI State and Command Feedback

### Command Button Behavior

| UI Element | Default Color | While In Progress | Finished Success | Finished Failure | Notes |
| --- | --- | --- | --- | --- | --- |
| Command button | Blue | Gray and visually pressed | Return to default blue unpressed state | Return to default blue unpressed state | The temporary in-flight color indicates that the requested command or state transition is still running. |

### Server State Color Behavior

| Server State Visual | Meaning | Notes |
| --- | --- | --- |
| Dark blue | Inactive / default after reset | The state is not currently executing and has no completed-success latch. |
| Blinking green | In progress | The state is currently executing and has not yet finished. |
| Red | Finished state with failure | The state finished and the result is failure or fault. |
| Light green | Finished state with success | The state finished and the result is success. |

### UI Grouping Rule

- Server-state indications must appear in a dedicated titled group box named `Server States`.
- Command controls and server-state indications must remain visually distinct so actions are not confused with state reporting.
- The logger panel should remain separate from the server-state group and continue to display the latest rolling transport history.

## Simulator Runtime Error Mapping

| Condition | Simulator Error Text | Notes |
| --- | --- | --- |
| Serial port unavailable during open/initialize | `COM port not found` | The simulator host can diagnose this locally because it owns the COM port. |
| Empty or unavailable mirrored Wi-Fi SSID | `Wi-Fi AP is offline` | Used when the bridge-side AP contract is invalid before initialize can continue. |
| TCP endpoint invalid or bridge reports listener failure | `TCP server not found` | Reserved for bridge-side/server-listener availability faults. |
| Stage fails without a stronger category | `generic unknown failure` | Fallback error for stage-specific failures without trustworthy root-cause evidence. |
