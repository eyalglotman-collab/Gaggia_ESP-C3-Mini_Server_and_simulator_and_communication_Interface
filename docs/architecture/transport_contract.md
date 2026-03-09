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

## State Definitions

| State | Purpose | Entry Actions | Exit Conditions |
| --- | --- | --- | --- |
| `reset` | Clear session state and run self-test. | Clear counters, buffers, stale link ownership, load parameters. | Self-test complete and parameters available. |
| `initialize` | Prepare transport resources without claiming a healthy link. | Validate configuration, prepare parser, prepare Wi-Fi/TCP roles and timers. | Configuration valid and resources ready, or initialization fault occurs. |
| `connect` | Establish and supervise the active low-level link. | Open/accept session, start keep-alive cadence, enforce CRC and sequencing. | Controlled disconnect or fault. |
| `disconnect` | Perform controlled teardown. | Stop forwarding, close transport cleanly, preserve reason. | Teardown complete or teardown fault occurs. |
| `error` | Latch low-level fault and block normal traffic. | Preserve error reason and last counters, stop forwarding payloads. | Explicit `reset` or `initialize` command only. |

## Transition Table

| Current State | Trigger | Guard / Condition | Action | Next State | Timeout / Failure Behavior |
| --- | --- | --- | --- | --- | --- |
| `reset` | Self-test complete | Parameters valid | Prepare initialization inputs | `initialize` | Self-test failure moves to `error`. |
| `initialize` | Initialize command completed | Configuration valid | Arm transport resources | `connect` | Validation or bring-up failure moves to `error`. |
| `connect` | Disconnect command | Intentional shutdown requested | Controlled teardown | `disconnect` | Teardown failure moves to `error`. |
| `connect` | Fault detected | CRC fault, watchdog timeout, malformed frame, transport loss | Latch fault and stop forwarding | `error` | Fault is terminal until explicit recovery. |
| `disconnect` | Teardown complete | Resources released | Return to clean baseline | `reset` | Incomplete teardown moves to `error`. |
| `error` | Recovery command | Explicit hard recovery | Clear fault and restart stack | `reset` | No implicit recovery allowed. |
| `error` | Recovery command | Explicit soft recovery | Re-prepare resources without full reset | `initialize` | No implicit recovery allowed. |

## Packet Definitions

| Packet | Purpose | Sender | Receiver | Required Fields | Normal Response | Timeout Rule | Error Handling |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `RESET` | Force hard reset and self-test. | Supervisory host | Low-level peer | `protocol_version`, `message_type`, reset profile/parameters, CRC | `RESET_ACK` | Supervisor expects bounded response time from reset path. | Failure enters `error`. |
| `INITIALIZE` | Prepare low-level resources. | Supervisory host | Low-level peer | endpoint/role parameters, watchdog settings, CRC | `INITIALIZE_ACK` | Initialization must complete before connect window expires. | Validation failure enters `error`. |
| `CONNECT` | Enter active session. | Supervisory host | Low-level peer | connection role or endpoint reference, CRC | `CONNECT_ACK` | Session establishment timeout enters `error`. | Socket/join failure enters `error`. |
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
| Wi-Fi association failure | Bridge-side initialize/connect | Latch fault with Wi-Fi status | `initialize` or `reset` |
| TCP session loss | Bridge-side connect state | Latch fault and stop forwarding | `initialize` then `connect`, or `reset` |
| Malformed packet / unsupported version | Parser | Reject packet and latch fault | `reset` after protocol correction |
| Intentional disconnect | Supervisor | Controlled shutdown | `reset` then normal reconnect sequence |