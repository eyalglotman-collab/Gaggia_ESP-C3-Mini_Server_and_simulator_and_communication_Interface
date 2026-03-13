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
| `ServerLiveInteger` | ESP32-C3 bridge/server side | The bridge owns keepalive initiation toward the client session and sends the next authoritative server-side value first. |
| `ClientLiveInteger` | ESP32-S3 client side | The client validates `ServerLiveInteger`, increments `ClientLiveInteger`, and returns it to the bridge. |
| CRC validation | Low-level transport layer | FastAPI and high-level simulator logic should not re-implement integrity checks. |
| Keepalive/watchdog timing | ESP32-C3 bridge/server side and ESP32-S3 client side | The bridge owns the active keepalive cadence and timeout detection for the server half of the link; the client validates and responds. |
| Recovery decision | Supervisory host logic | The Python host mirrors bridge-reported faults, counts sequential keepalive failures, and enters `wait_for_com_reset` after threshold. |

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
| `wifi_ssid` | `EyalSimulatorAP` | Bridge-side Wi-Fi network identifier mirrored from the client contract and advertised by the ESP32-C3 as a visible SoftAP (`ssid_hidden = 0`). |
| `wifi_password` | `espresso1234` | Bridge-side Wi-Fi credential mirrored from the client contract. |
| `server_ip` | `192.168.4.1` | Default bridge-side/server endpoint address used during connect. |
| `server_port` | `3333` | TCP endpoint used for the low-level transport session. |
| `wifi_connect_timeout_ms` | `10000` | Bound on low-level Wi-Fi association/visibility preparation. |
| `tcp_connect_timeout_ms` | `3000` | Bound on TCP session establishment after Wi-Fi is ready. |
| `keepalive_period_ms` | `100` | Bridge-driven keep-alive cadence. |

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
| `reset` | Stop transmission, clear buffers, and re-initialize the ESP controller. | Clear counters, buffers, stale link ownership, and active supervision before issuing `RESET`, then arm automatic progression into `initialize`. | Reset-send succeeds and automatic initialize can begin. |
| `initialize` | Load the ESP controller with mirrored transport constants. | Validate mirrored COM/Wi-Fi/TCP configuration and send Wi-Fi/server constants. | Automatic hand-off into `connect`, or a blocking setup fault requires reset. |
| `connect` | Wait for the ESP/controller stack to report real client connection success. | Hold the mirrored Wi-Fi-ready/connect-attempt context, zero both counters, and wait for an explicit client-connected indication or mirrored keepalive/data from the ESP side. | Explicit client connection success or first valid keepalive promotes to `keepalive`, or connect retry is scheduled. |
| `keepalive` | Supervise the active low-level connection. | Mirror bridge-owned keepalive progress, track `ServerLiveInteger` and `ClientLiveInteger`, and watch for missed responses. | Valid keepalive clears `ConnectionFault`; repeated keepalive loss retries through `connect` until reset is required. |
| `wait_for_com_reset` | Stop automatic retry churn after repeated keepalive failures or blocking COM/runtime faults. | Preserve `ConnectionFault`, keep the latest failure reason visible, and wait for explicit operator reset. | `Reset Communication` returns to `reset`. |

## Python Runtime Responsibilities

The Python simulator runtime remains active, but only as supervisory logic around the bridge-owned transport:

- own the COM-port handle and serial lifetime through `server/transport/serial_link.py`
- expose operator APIs in `server/api/routes.py`
- mirror authoritative bridge state and counters into UI snapshots
- validate mirrored configuration before sending `INITIALIZE`
- drive the automatic host-visible progression `reset -> initialize -> connect`
- count sequential bridge-reported keepalive failures and latch `ConnectionFault`
- gate `DATA` so it is sent only after the bridge has proven `keepalive`

The Python runtime must not:

- originate low-level keepalive timing
- derive transport timeout/fault conclusions from browser polling cadence
- invent `ServerLiveInteger` or `ClientLiveInteger` values

## Transition Table

| Current State | Trigger | Guard / Condition | Action | Next State | Timeout / Failure Behavior |
| --- | --- | --- | --- | --- | --- |
| `reset` | Reset command | Operator requests hard recovery or fresh start | Stop transmission, clear buffers, issue `RESET` to the ESP controller, clear `ConnectionFault`, and arm automatic initialize | `reset` | Reset-send failure moves to `wait_for_com_reset`. |
| `reset` | Automatic progression | Reset completed and the runtime still owns a valid transport path | Send mirrored transport constants to the ESP controller | `initialize` | Validation, COM availability, or initialize transmit failure moves to `wait_for_com_reset`. |
| `initialize` | Automatic progression | Mirrored constants were sent successfully | Automatically issue `CONNECT`, zero both counters on bridge entry to `connect`, and wait for the ESP response | `connect` | Connect transmit failure moves to `wait_for_com_reset`. |
| `connect` | Client-connected response or first mirrored keepalive received | TCP session has been proven alive | Enable keepalive supervision | `keepalive` | Connect timeout retries through `connect`. |
| `keepalive` | Valid `KEEPALIVE` or mirrored `DATA` received | Counter exchange succeeds | Clear `ConnectionFault`, zero failure counter, keep application payloads enabled | `keepalive` | Keepalive loss retries through `connect`. After 5 sequential keepalive failures, the simulator enters `wait_for_com_reset`. |
| `keepalive` | `DATA` command | Keepalive-ready connection is active | Send validated `DATA` while remaining in canonical keepalive state | `keepalive` | Payload attempt without keepalive-ready link moves to `wait_for_com_reset`. |
| `wait_for_com_reset` | Recovery command | Explicit hard recovery | Clear fault and restart stack | `reset` | No implicit recovery allowed. |

## Packet Definitions

| Packet | Purpose | Sender | Receiver | Required Fields | Normal Response | Timeout Rule | Error Handling |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `RESET` | Force hard reset and self-test. | Supervisory host | Low-level peer | `protocol_version`, `message_type`, reset profile/parameters, CRC | `RESET_ACK` | Supervisor expects bounded response time from reset path. | Host-side reset transmit failures latch `ConnectionFault` and move to `wait_for_com_reset`. |
| `INITIALIZE` | Prepare low-level resources. | Supervisory host | Low-level peer | serial port, Wi-Fi SSID/password, server IP/port, watchdog settings, CRC | `INITIALIZE_ACK` | Initialization must complete before connect window expires. | Invalid mirrored config or initialize transmit failure moves to `wait_for_com_reset`. |
| `CONNECT` | Enter active session. | Supervisory host | Low-level peer | connection role or endpoint reference, CRC | `CONNECT_ACK` followed by `client_connected` or mirrored `KEEPALIVE` | Session-establishment timeout retries through `connect`. | COM/runtime transmit failure enters `wait_for_com_reset`. |
| `KEEPALIVE` | Prove synchronized server/client forward progress. | ESP32-C3 bridge/server side | ESP32-S3 client side | current `ServerLiveInteger`, latest `ClientLiveInteger`, sequence, CRC | client returns `KEEPALIVE` with incremented `ClientLiveInteger`; bridge validates it and advances the next `ServerLiveInteger` | Every 100 mSec. | Missing counter progression retries through `connect` and enters `wait_for_com_reset` after 5 repeated failures. |
| `DATA` | Carry application payload after validation. | Either side | Peer | payload, sequence, CRC | `ACK` or application response | Normal transport timeout policy applies. | Invalid frame is rejected before upper layer sees payload. |
| `ERROR` | Report low-level fault detail without introducing a host `error` state. | Faulting side | Supervisory peer | error code, state, last counters, summary, CRC | recovery logic or explicit reset | Immediate supervisory review required. | Bridge-reported keepalive loss retries through `connect`; blocking COM/runtime faults latch `ConnectionFault` and move the host to `wait_for_com_reset`. |

## Timing Rules

- Keep-alive cadence is `100 mSec`.
- The ESP32-C3 bridge/server side owns keepalive initiation and resets both counters to `0` every time it enters `connect`.
- The ESP32-S3 client side zeros both counters during `initialize`.
- The ESP32-S3 client side increments `ClientLiveInteger` only after validating the current `ServerLiveInteger`.
- After validating the returned `ClientLiveInteger`, the bridge advances `ServerLiveInteger` and sends the next keepalive.
- The simulator host monitors the authoritative bridge counters over USB; it does not originate keepalive traffic itself.
- The simulator host latches `ConnectionFault` after 5 sequential keepalive failures and waits in `wait_for_com_reset`.
- Both counters wrap to `0` on overflow.

## Failure Modes

| Failure Mode | Detection Point | Required Action | Allowed Recovery |
| --- | --- | --- | --- |
| CRC failure | Low-level frame parser | Drop frame and retry through `connect` | automatic retry or `reset` after threshold |
| Bridge keepalive watchdog failure | ESP32-C3 transport controller | Report `keepalive_supervision_lost`, close stale TCP session, and let the host retry through `connect` | automatic retry until threshold, then `reset` |
| Device-side keepalive failure | ESP32-S3 client transport layer | Stop trusting link and re-enter `connect` on the client side | automatic retry until threshold, then `reset` |
| USB COM loss | Host or bridge | Close stale COM handle and wait for reopen | `reset` after COM recovery |
| COM port not found | Simulator host open/initialize path | Latch explicit COM availability fault and move to `wait_for_com_reset` | `reset` after COM recovery |
| Wi-Fi association failure | Bridge-side initialize/connect | Retry through `connect` while retaining mirrored configuration | automatic retry |
| Configured AP offline / not visible | Bridge-side initialize/connect or mirrored simulator validation | Retry through `connect` or block initialize if Wi-Fi is disabled | automatic retry or `reset` after operator action |
| TCP server not found / not listening | Bridge-side connect | Retry through `connect` | automatic retry |
| Keepalive response timeout | Keepalive supervision | Count sequential keepalive failure and retry through `connect` | automatic retry until threshold, then `reset` |
| Generic unknown transport failure | Any stage without stronger evidence | Preserve stage-specific fault text and retry or block as appropriate | retry or `reset` |
| TCP session loss | Bridge-side keepalive state | Retry through `connect` | automatic retry |
| Malformed packet / unsupported version | Parser | Reject packet and retry link bring-up | automatic retry or `reset` after threshold |

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
- All editable text controls in the simulator UI must use a bright fill with a light visible frame so operators can immediately distinguish writable fields from static text surfaces.

## Simulator Runtime Error Mapping

| Condition | Simulator Error Text | Notes |
| --- | --- | --- |
| Serial port unavailable during open/initialize | `COM port not found` | The simulator host can diagnose this locally because it owns the COM port. |
| Empty or unavailable mirrored Wi-Fi SSID | `Wi-Fi AP is offline` | Used when the bridge-side AP contract is invalid before initialize can continue. |
| Host-side Wi-Fi toggle is off | `Wi-Fi is disabled` | Used when the operator intentionally disabled the bridge SoftAP through the simulator UI. |
| TCP endpoint invalid or bridge reports listener failure | `TCP server not found` | Reserved for bridge-side/server-listener availability faults. |
| Stage fails without a stronger category | `generic unknown failure` | Fallback error for stage-specific failures without trustworthy root-cause evidence. |
