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
| Recovery decision | Supervisory host logic | Only explicit `reset` or `initialize` recovers from `error`. |

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
| `reset` | Clear session state and run self-test. | Clear counters, buffers, stale link ownership, load parameters. | Self-test complete and parameters available. |
| `initialize` | Prepare transport resources without claiming a healthy link. | Validate mirrored COM/Wi-Fi/TCP configuration, prepare parser, and hand bridge settings to the ESP32-C3 transport controller. | Configuration valid and resources ready, or initialization fault occurs. |
| `connect` | Establish and supervise the active low-level link. | Enter Wi-Fi-ready state, attempt the active TCP session, start keep-alive cadence, enforce CRC and sequencing. | Controlled disconnect or fault. |
| `disconnect` | Perform controlled teardown. | Stop forwarding, close transport cleanly, preserve reason. | Teardown complete or teardown fault occurs. |
| `error` | Latch low-level fault and block normal traffic. | Preserve error reason and last counters, stop forwarding payloads. | Explicit `reset` or `initialize` command only. |

## Transition Table

| Current State | Trigger | Guard / Condition | Action | Next State | Timeout / Failure Behavior |
| --- | --- | --- | --- | --- | --- |
| `reset` | Self-test complete | Parameters valid | Prepare initialization inputs | `initialize` | Self-test failure moves to `error`. |
| `initialize` | Initialize command completed | COM is available and mirrored Wi-Fi/TCP configuration is coherent | Arm bridge resources and hand off Wi-Fi/TCP settings | `connect` | Validation, COM availability, or bridge bring-up failure moves to `error`. |
| `connect` | Disconnect command | Intentional shutdown requested | Controlled teardown | `disconnect` | Teardown failure moves to `error`. |
| `connect` | Fault detected | CRC fault, watchdog timeout, malformed frame, transport loss | Latch fault and stop forwarding | `error` | Fault is terminal until explicit recovery. |
| `disconnect` | Teardown complete | Resources released | Return to clean baseline | `reset` | Incomplete teardown moves to `error`. |
| `error` | Recovery command | Explicit hard recovery | Clear fault and restart stack | `reset` | No implicit recovery allowed. |
| `error` | Recovery command | Explicit soft recovery | Re-prepare resources without full reset | `initialize` | No implicit recovery allowed. |

## Packet Definitions

| Packet | Purpose | Sender | Receiver | Required Fields | Normal Response | Timeout Rule | Error Handling |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `RESET` | Force hard reset and self-test. | Supervisory host | Low-level peer | `protocol_version`, `message_type`, reset profile/parameters, CRC | `RESET_ACK` | Supervisor expects bounded response time from reset path. | Failure enters `error`. |
| `INITIALIZE` | Prepare low-level resources. | Supervisory host | Low-level peer | serial port, Wi-Fi SSID/password, server IP/port, watchdog settings, CRC | `INITIALIZE_ACK` | Initialization must complete before connect window expires. | Validation failure enters `error`. |
| `CONNECT` | Enter active session. | Supervisory host | Low-level peer | connection role or endpoint reference, CRC | `CONNECT_ACK` | Session establishment timeout enters `error`. | Wi-Fi/TCP socket failure enters `error`. |
| `DISCONNECT` | Controlled teardown. | Supervisory host | Low-level peer | disconnect reason, CRC | `DISCONNECT_ACK` | Teardown must complete in bounded time. | Teardown failure enters `error`. |
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
| CRC failure | Low-level frame parser | Drop frame and latch fault | `reset` or `initialize` |
| Host watchdog failure | ESP32-C3 transport controller | Assume host stalled and latch fault | `reset` or `initialize` after host recovers |
| Device watchdog failure | PC simulator host | Stop trusting link and latch fault | `reset` |
| USB COM loss | Host or bridge | Stop transport and latch fault | `reset` after COM recovery |
| COM port not found | Simulator host open/initialize path | Latch explicit COM availability fault before bridge initialization continues | `reset` after COM recovery |
| Wi-Fi association failure | Bridge-side initialize/connect | Latch fault with Wi-Fi status | `initialize` or `reset` |
| Configured AP offline / not visible | Bridge-side initialize/connect or mirrored simulator validation | Latch explicit AP-not-visible fault before claiming Wi-Fi-ready state | `reset` after RF or configuration changes |
| TCP server not found / not listening | Bridge-side connect | Latch explicit server/listener availability fault | `initialize`, corrected endpoint, or `reset` |
| Generic unknown transport failure | Any stage without stronger evidence | Latch stage-specific fault and stop progressing state | `reset` or `initialize` |
| TCP session loss | Bridge-side connect state | Latch fault and stop forwarding | `initialize` then `connect`, or `reset` |
| Malformed packet / unsupported version | Parser | Reject packet and latch fault | `reset` after protocol correction |
| Intentional disconnect | Supervisor | Controlled shutdown | `reset` then normal reconnect sequence |

## Simulator UI State and Command Feedback

### Command Button Behavior

| UI Element | Default Color | While In Progress | Finished Success | Finished Failure | Notes |
| --- | --- | --- | --- | --- | --- |
| Command button | Blue | Gray and visually pressed | Return to default blue unpressed state | Return to default blue unpressed state | The temporary in-flight color indicates that the requested command or state transition is still running. |

### Machine State Color Behavior

| Machine State Visual | Meaning | Notes |
| --- | --- | --- |
| Dark blue | Inactive / default after reset | The state is not currently executing and has no completed-success latch. |
| Blinking green | In progress | The state is currently executing and has not yet finished. |
| Red | Finished state with failure | The state finished and the result is failure or fault. |
| Light green | Finished state with success | The state finished and the result is success. |

### UI Grouping Rule

- Machine-state indications must appear in a dedicated titled group box named `Machine State`.
- Command controls and machine-state indications must remain visually distinct so actions are not confused with state reporting.
- The logger panel should remain separate from the machine-state group and continue to display the latest rolling transport history.

## Simulator Runtime Error Mapping

| Condition | Simulator Error Text | Notes |
| --- | --- | --- |
| Serial port unavailable during open/initialize | `COM port not found` | The simulator host can diagnose this locally because it owns the COM port. |
| Empty or unavailable mirrored Wi-Fi SSID | `Wi-Fi AP is offline` | Used when the bridge-side AP contract is invalid before initialize can continue. |
| TCP endpoint invalid or bridge reports listener failure | `TCP server not found` | Reserved for bridge-side/server-listener availability faults. |
| Stage fails without a stronger category | `generic unknown failure` | Fallback error for stage-specific failures without trustworthy root-cause evidence. |
