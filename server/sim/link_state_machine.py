"""Low-level transport state machine for the simulator template."""

from __future__ import annotations

from collections import deque
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from threading import Lock

from server.transport.frame_codec import Frame, MessageType
from server.transport.serial_link import SerialLinkSnapshot, serial_link_manager

WATCHDOG_MS = 100
WATCHDOG_GRACE_MS = 350


class LinkState(StrEnum):
    RESET = "reset"
    INITIALIZE = "initialize"
    CONNECT = "connect"
    DISCONNECT = "disconnect"
    ERROR = "error"


@dataclass(slots=True)
class LinkSnapshot:
    current_state: str
    serial_port: str
    last_transition_at: str
    watchdog_armed: bool
    host_live_integer: int
    device_live_integer: int
    sequence: int
    last_error: str
    important_data: dict[str, str]
    transport: dict[str, object]
    logs: list[str]
    available_states: list[str]


class LinkRuntime:
    """@brief Own the low-level simulator transport workflow.

    @details The runtime focuses only on the serial link, frame flow, counters,
    and watchdog behavior. It intentionally avoids higher-level brew logic so
    the PC host and future ESP32-C3 bridge can be brought up incrementally.
    """

    def __init__(self) -> None:
        self._lock = Lock()
        self._logs: deque[str] = deque(maxlen=400)
        self._current_state = LinkState.RESET
        self._last_transition_at = self._timestamp()
        self._host_live_integer = 0
        self._device_live_integer = 0
        self._sequence = 0
        self._watchdog_armed = False
        self._last_keepalive_tx: datetime | None = None
        self._last_valid_rx: datetime | None = None
        self._last_error = ""
        self._append_log("Transport runtime ready. Default state is reset.")

    def _timestamp(self) -> str:
        return datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%SZ")

    def _append_log(self, message: str) -> None:
        self._logs.append(f"[{self._timestamp()}] {message}")

    def _set_state(self, state: LinkState, message: str, error: str = "") -> None:
        self._current_state = state
        self._last_transition_at = self._timestamp()
        self._last_error = error
        self._append_log(message if not error else f"{message} ({error})")

    def _next_sequence(self) -> int:
        self._sequence = (self._sequence + 1) % 65536
        return self._sequence

    def _poll_received_frames_locked(self) -> None:
        for frame in serial_link_manager.pop_received_frames():
            self._last_valid_rx = datetime.now(UTC)
            self._device_live_integer = max(self._device_live_integer, frame.device_live_integer)
            self._append_log(f"Received {frame.message_type.name} seq={frame.sequence} host={frame.host_live_integer} device={frame.device_live_integer}.")
            if frame.message_type in (MessageType.ACK, MessageType.KEEPALIVE, MessageType.DATA):
                self._watchdog_armed = True

    def _evaluate_watchdog_locked(self) -> None:
        if self._current_state is not LinkState.CONNECT or not self._watchdog_armed:
            return
        if self._last_valid_rx is None:
            return
        if datetime.now(UTC) - self._last_valid_rx > timedelta(milliseconds=WATCHDOG_GRACE_MS):
            self._watchdog_armed = False
            self._set_state(LinkState.ERROR, "Watchdog expired while waiting for peer frame progress.", "no RX progress")

    def _send_command_locked(self, message_type: MessageType, payload_text: str = "") -> None:
        frame = Frame(
            message_type=message_type,
            host_live_integer=self._host_live_integer,
            device_live_integer=self._device_live_integer,
            sequence=self._next_sequence(),
            payload=payload_text.encode("utf-8"),
        )
        serial_link_manager.send_frame(frame)
        self._append_log(f"Sent {message_type.name} seq={frame.sequence} host={frame.host_live_integer}.")

    def configure_port(self, port_name: str) -> LinkSnapshot:
        with self._lock:
            serial_link_manager.configure_port(port_name)
            self._append_log(f"Configured serial port to {port_name.strip() or 'COM4'}.")
            return self._snapshot_locked()

    def open_transport(self, port_name: str | None = None) -> LinkSnapshot:
        with self._lock:
            if port_name:
                serial_link_manager.configure_port(port_name)
            serial_link_manager.open_port()
            self._append_log("Serial transport opened.")
            return self._snapshot_locked()

    def close_transport(self) -> LinkSnapshot:
        with self._lock:
            serial_link_manager.close_port()
            self._watchdog_armed = False
            self._append_log("Serial transport closed.")
            return self._snapshot_locked()

    def reset(self) -> LinkSnapshot:
        with self._lock:
            self._poll_received_frames_locked()
            self._host_live_integer = 0
            self._device_live_integer = 0
            self._watchdog_armed = False
            self._last_keepalive_tx = None
            self._last_valid_rx = None
            self._send_command_locked(MessageType.RESET, "reset")
            self._set_state(LinkState.RESET, "Transport reset command issued.")
            return self._snapshot_locked()

    def initialize(self) -> LinkSnapshot:
        with self._lock:
            self._poll_received_frames_locked()
            self._send_command_locked(MessageType.INITIALIZE, "initialize")
            self._set_state(LinkState.INITIALIZE, "Initialization command issued.")
            return self._snapshot_locked()

    def connect(self) -> LinkSnapshot:
        with self._lock:
            self._poll_received_frames_locked()
            self._send_command_locked(MessageType.CONNECT, "connect")
            self._last_keepalive_tx = datetime.now(UTC)
            self._watchdog_armed = False
            self._set_state(LinkState.CONNECT, "Connect command issued. Waiting for peer frame progress.")
            return self._snapshot_locked()

    def disconnect(self) -> LinkSnapshot:
        with self._lock:
            self._poll_received_frames_locked()
            self._send_command_locked(MessageType.DISCONNECT, "disconnect")
            self._watchdog_armed = False
            self._set_state(LinkState.DISCONNECT, "Disconnect command issued.")
            return self._snapshot_locked()

    def send_keepalive(self) -> LinkSnapshot:
        with self._lock:
            self._poll_received_frames_locked()
            self._host_live_integer += 1
            self._send_command_locked(MessageType.KEEPALIVE, "keepalive")
            self._last_keepalive_tx = datetime.now(UTC)
            if self._current_state is LinkState.CONNECT and self._last_valid_rx is None:
                self._append_log("Keepalive sent; watchdog not armed until a peer frame is received.")
            return self._snapshot_locked()

    def get_snapshot(self) -> LinkSnapshot:
        with self._lock:
            self._poll_received_frames_locked()
            self._evaluate_watchdog_locked()
            return self._snapshot_locked()

    def _snapshot_locked(self) -> LinkSnapshot:
        transport_snapshot: SerialLinkSnapshot = serial_link_manager.get_snapshot()
        important_data = {
            "Current State": self._current_state.value,
            "Serial Port": transport_snapshot.port_name,
            "Port Open": "Yes" if transport_snapshot.port_open else "No",
            "HostLiveInteger": str(self._host_live_integer),
            "DeviceLiveInteger": str(self._device_live_integer),
            "Sequence": str(self._sequence),
            "Watchdog Armed": "Yes" if self._watchdog_armed else "No",
            "Last Transition": self._last_transition_at,
            "Last RX": transport_snapshot.last_rx_at,
            "Last TX": transport_snapshot.last_tx_at,
            "Last Error": self._last_error or transport_snapshot.last_error or "None",
        }
        combined_logs = list(self._logs) + serial_link_manager.get_logs()[-100:]
        return LinkSnapshot(
            current_state=self._current_state.value,
            serial_port=transport_snapshot.port_name,
            last_transition_at=self._last_transition_at,
            watchdog_armed=self._watchdog_armed,
            host_live_integer=self._host_live_integer,
            device_live_integer=self._device_live_integer,
            sequence=self._sequence,
            last_error=self._last_error or transport_snapshot.last_error,
            important_data=important_data,
            transport=asdict(transport_snapshot),
            logs=combined_logs[-250:],
            available_states=[state.value for state in LinkState],
        )


link_runtime = LinkRuntime()