"""Low-level transport state machine for the simulator template."""

from __future__ import annotations

from collections import deque
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from threading import Lock
from traceback import format_exception_only

from server.transport.frame_codec import Frame, MessageType
from server.transport.serial_link import SerialLinkSnapshot, serial_link_manager

WATCHDOG_MS = 100
WATCHDOG_GRACE_MS = 350
CONNECT_SUCCESS_PAYLOADS = {"client_connected", "connect_success", "tcp_connected"}


class LinkState(StrEnum):
    RESET = "reset"
    INITIALIZE = "initialize"
    CONNECT = "connect"
    KEEPALIVE = "keepalive"
    SEND_DATA = "send_data"
    ERROR = "error"


@dataclass(slots=True)
class TransportConfig:
    """@brief Store the mirrored low-level Wi-Fi/TCP transport configuration.

    @details The simulator keeps the same essential configuration fields as the
    client-side communication module so both repositories describe the same
    transport contract even though the simulator host does not directly own the
    Wi-Fi radio.
    """

    serial_port: str = "COM4"
    wifi_ssid: str = "EyalSimulatorAP"
    wifi_password: str = "espresso1234"
    server_ip: str = "192.168.4.1"
    server_port: int = 3333
    wifi_connect_timeout_ms: int = 10000
    tcp_connect_timeout_ms: int = 3000
    keepalive_period_ms: int = WATCHDOG_MS


@dataclass(slots=True)
class LinkSnapshot:
    current_state: str
    serial_port: str
    config: dict[str, object]
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
        self._logs: deque[str] = deque(maxlen=2000)
        self._current_state = LinkState.RESET
        self._config = TransportConfig()
        self._last_transition_at = self._timestamp()
        self._host_live_integer = 0
        self._device_live_integer = 0
        self._sequence = 0
        self._watchdog_armed = False
        self._last_keepalive_tx: datetime | None = None
        self._last_valid_rx: datetime | None = None
        self._connect_requested_at: datetime | None = None
        self._keepalive_ack_pending = False
        self._pending_auto_stage: LinkState | None = None
        self._last_error = ""
        self._last_monitor_event = "Simulator monitor ready."
        self._last_monitor_at = self._timestamp()
        self._last_snapshot_poll_at: datetime | None = None
        self._wifi_ready = False
        self._wifi_connected = False
        self._tcp_connected = False
        self._bridge_ready = False
        self._initialize_completed = False
        self._connect_completed = False
        self._send_data_enabled = False
        self._append_log("Transport runtime ready. Default state is reset.")

    def _timestamp(self) -> str:
        now = datetime.now(UTC)
        return f"{now:%H:%M:%S}.{now.microsecond // 10000:02d}"

    def _append_log(self, message: str) -> None:
        self._logs.appendleft(f"[{self._timestamp()}] {message}")

    def _combined_logs_locked(self) -> list[str]:
        """@brief Return the UI logger feed in newest-to-oldest order.

        @details Both runtime logs and serial-manager logs are stored with the
        newest entry first. The UI logger is capped to the last 2000 messages
        across both sources so the browser keeps a bounded rolling history.
        """

        combined_logs = list(self._logs) + serial_link_manager.get_logs()
        combined_logs.sort(reverse=True)
        return combined_logs[:2000]

    def _set_state(self, state: LinkState, message: str, error: str = "") -> None:
        self._current_state = state
        self._last_transition_at = self._timestamp()
        self._last_error = error
        self._append_log(message if not error else f"{message} ({error})")

    def _clear_runtime_flow_locked(self) -> None:
        """@brief Clear active low-level flow bookkeeping.

        @details Reset and fault paths use this helper to stop carry-over
        timing and gating state before a new ESP controller session begins.
        """

        self._watchdog_armed = False
        self._last_keepalive_tx = None
        self._last_valid_rx = None
        self._connect_requested_at = None
        self._keepalive_ack_pending = False
        self._initialize_completed = False
        self._connect_completed = False
        self._send_data_enabled = False
        self._pending_auto_stage = None

    def _set_error(self, message: str) -> None:
        """@brief Latch an error and move the low-level runtime into `error`.

        @details Centralizes error transitions so the snapshot keeps the same
        last-error semantics across COM, Wi-Fi configuration, and TCP stages.
        """

        self._clear_runtime_flow_locked()
        self._tcp_connected = False
        self._wifi_connected = False
        self._set_state(LinkState.ERROR, "Transport fault latched.", message)

    def _try_send_command_locked(
        self,
        message_type: MessageType,
        payload_text: str,
        failure_prefix: str,
    ) -> bool:
        """@brief Send one command and convert transport failures into `error`.

        @details This helper keeps the API snapshot-based even when COM writes
        fail, so the operator sees the stage-specific fault reason in the UI.
        """

        try:
            self._send_command_locked(message_type, payload_text)
        except RuntimeError as exc:
            self._set_error(f"{failure_prefix}: {exc}")
            return False
        return True

    def _begin_initialize_locked(self, reason: str) -> None:
        """@brief Start the initialize stage of the automatic controller flow.

        @details This stage validates configuration, loads the ESP controller
        with mirrored constants, and schedules the automatic connect step.
        """

        config_error = self._validate_config_locked()
        if config_error is not None:
            self._set_error(config_error)
            return
        if not serial_link_manager.get_snapshot().port_open:
            self._set_error("COM port not found")
            return

        self._wifi_ready = True
        self._wifi_connected = False
        self._tcp_connected = False
        self._bridge_ready = True
        self._set_state(LinkState.INITIALIZE, reason)
        if not self._try_send_command_locked(
            MessageType.INITIALIZE,
            self._build_initialize_payload(),
            "initialize transmit failed",
        ):
            return
        self._pending_auto_stage = LinkState.CONNECT

    def _begin_connect_locked(self, reason: str) -> None:
        """@brief Start the connect stage after initialize has completed.

        @details Connect is automatically entered by the server runtime once
        the mirrored constants were sent successfully to the ESP controller.
        """

        if not self._wifi_ready:
            self._set_error("initialize did not complete before connect")
            return
        if not self._try_send_command_locked(
            MessageType.CONNECT,
            f"server={self._config.server_ip}:{self._config.server_port}",
            "connect transmit failed",
        ):
            return
        self._pending_auto_stage = None
        self._initialize_completed = True
        self._connect_requested_at = datetime.now(UTC)
        self._wifi_connected = True
        self._tcp_connected = False
        self._set_state(LinkState.CONNECT, reason)

    def _advance_automatic_flow_locked(self) -> None:
        """@brief Progress the automatic reset-to-keepalive sequence.

        @details The runtime advances one stage per snapshot poll so the UI can
        observe reset, initialize, and connect as distinct server states.
        """

        if self._current_state is LinkState.ERROR:
            return
        if self._current_state is LinkState.RESET and self._pending_auto_stage is LinkState.INITIALIZE:
            self._begin_initialize_locked("Automatic progression entered initialize.")
            return
        if self._current_state is LinkState.INITIALIZE and self._pending_auto_stage is LinkState.CONNECT:
            self._begin_connect_locked("Initialize completed. Waiting for connect response.")

    def note_monitor_event(self, category: str, message: str, *, throttle_snapshot: bool = False) -> None:
        """@brief Record a monitor-visible event into the runtime log stream.

        @details This gives the UI logger a consistent place to show backend
        monitoring events such as API invokes, throttled snapshot polls, and
        fail-safe transitions while avoiding excessive log spam from frequent
        `/api/link` refreshes.
        """

        with self._lock:
            now = datetime.now(UTC)
            if throttle_snapshot and self._last_snapshot_poll_at is not None:
                if now - self._last_snapshot_poll_at < timedelta(seconds=5):
                    return
            if throttle_snapshot:
                self._last_snapshot_poll_at = now
            self._last_monitor_event = f"{category}: {message}"
            self._last_monitor_at = self._timestamp()
            self._append_log(f"[monitor] {self._last_monitor_event}")

    def capture_internal_failure(self, context: str, exc: Exception) -> LinkSnapshot:
        """@brief Latch an unexpected internal exception as a safe snapshot.

        @details This fail-safe path keeps the API responsive even if an
        unexpected runtime exception occurs inside a transport action.
        """

        with self._lock:
            detail = "".join(format_exception_only(type(exc), exc)).strip()
            self._last_monitor_event = f"fail-safe: {context}"
            self._last_monitor_at = self._timestamp()
            self._set_error(f"{context}: {detail or 'generic unknown failure'}")
            return self._snapshot_locked()

    def _next_sequence(self) -> int:
        self._sequence = (self._sequence + 1) % 65536
        return self._sequence

    def _build_initialize_payload(self) -> str:
        """@brief Serialize the transport configuration for the peer bridge.

        @details A simple key-value payload is sufficient for the current
        transport template and keeps the initialization command readable in
        logs while the protocol remains under active design.
        """

        return (
            f"ssid={self._config.wifi_ssid};"
            f"password={self._config.wifi_password};"
            f"server_ip={self._config.server_ip};"
            f"server_port={self._config.server_port};"
            f"wifi_timeout_ms={self._config.wifi_connect_timeout_ms};"
            f"tcp_timeout_ms={self._config.tcp_connect_timeout_ms};"
            f"keepalive_ms={self._config.keepalive_period_ms}"
        )

    def _validate_config_locked(self) -> str | None:
        """@brief Validate mirrored Wi-Fi/TCP configuration before initialize.

        @details The simulator host mirrors the client configuration contract
        and surfaces explicit configuration faults before trying to drive the
        bridge through the low-level initialize/connect sequence.
        """

        if not self._config.serial_port.strip():
            return "COM port not found"
        if not self._config.wifi_ssid.strip():
            return "Wi-Fi AP is offline"
        if not self._config.server_ip.strip():
            return "TCP server not found"
        if self._config.server_port <= 0 or self._config.server_port > 65535:
            return "generic unknown failure"
        if self._config.keepalive_period_ms <= 0:
            return "generic unknown failure"
        return None

    def _poll_received_frames_locked(self) -> None:
        """@brief Consume received frames and advance the server-side states.

        @details RX handling owns the asynchronous promotions from connect into
        keepalive, acknowledges keepalive progress, and enables send-data only
        after the ESP controller confirms the active transport session.
        """

        for frame in serial_link_manager.pop_received_frames():
            self._last_valid_rx = datetime.now(UTC)
            self._device_live_integer = max(self._device_live_integer, frame.device_live_integer)
            self._append_log(f"Received {frame.message_type.name} seq={frame.sequence} host={frame.host_live_integer} device={frame.device_live_integer}.")
            payload_text = frame.payload.decode("utf-8", errors="ignore").strip().lower()

            if frame.message_type == MessageType.ERROR:
                self._set_error(payload_text or "generic unknown failure")
                continue

            if frame.message_type in (MessageType.KEEPALIVE, MessageType.DATA):
                self._watchdog_armed = True
                self._wifi_connected = True
                self._tcp_connected = True
                self._bridge_ready = True

            if self._current_state is LinkState.CONNECT and payload_text == "connect_ack":
                self._wifi_connected = True
                self._tcp_connected = False
                self._bridge_ready = True
                self._append_log(
                    "Bridge acknowledged CONNECT, but the runtime is still waiting for explicit client connection success."
                )
                continue

            if self._current_state is LinkState.CONNECT and payload_text in CONNECT_SUCCESS_PAYLOADS:
                self._connect_requested_at = None
                self._keepalive_ack_pending = False
                self._pending_auto_stage = None
                self._connect_completed = True
                self._send_data_enabled = True
                self._watchdog_armed = True
                self._wifi_connected = True
                self._tcp_connected = True
                self._bridge_ready = True
                self._set_state(
                    LinkState.KEEPALIVE,
                    "Explicit client connection success received. Server state advanced to keepalive supervision.",
                )
                continue

            if self._current_state in (LinkState.KEEPALIVE, LinkState.SEND_DATA):
                self._keepalive_ack_pending = False
                if frame.message_type == MessageType.DATA:
                    self._send_data_enabled = True
                    self._set_state(
                        LinkState.SEND_DATA,
                        "ESP controller acknowledged data traffic. Send-data path remains enabled.",
                    )

    def _evaluate_watchdog_locked(self) -> None:
        """@brief Apply connect and keepalive supervision timers.

        @details The connect phase waits for an ESP-side connect response, and
        the keepalive path expects forward progress after each keepalive TX.
        Any missed deadline forces the runtime into `error`.
        """

        now = datetime.now(UTC)

        if self._current_state is LinkState.CONNECT and self._connect_requested_at is not None:
            if now - self._connect_requested_at > timedelta(milliseconds=self._config.tcp_connect_timeout_ms):
                self._set_error("TCP server not found")
                return

        if self._current_state not in (LinkState.KEEPALIVE, LinkState.SEND_DATA):
            return

        if self._keepalive_ack_pending and self._last_keepalive_tx is not None:
            if now - self._last_keepalive_tx > timedelta(milliseconds=WATCHDOG_GRACE_MS):
                self._set_error("keepalive response timeout")
                return

        if self._watchdog_armed and self._last_valid_rx is not None:
            if now - self._last_valid_rx > timedelta(milliseconds=WATCHDOG_GRACE_MS):
                self._set_error("keepalive supervision lost")

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
            self._config.serial_port = port_name.strip() or "COM4"
            serial_link_manager.configure_port(self._config.serial_port)
            self._append_log(f"Configured serial port to {self._config.serial_port}.")
            return self._snapshot_locked()

    def configure_transport(
        self,
        *,
        serial_port: str | None = None,
        wifi_ssid: str | None = None,
        wifi_password: str | None = None,
        server_ip: str | None = None,
        server_port: int | None = None,
        wifi_connect_timeout_ms: int | None = None,
        tcp_connect_timeout_ms: int | None = None,
        keepalive_period_ms: int | None = None,
    ) -> LinkSnapshot:
        """@brief Update the mirrored Wi-Fi/TCP transport configuration.

        @details The simulator UI and API use this call to keep the PC-host
        representation aligned with the ESP32-S3 client transport defaults and
        any future bridge-side configuration changes.
        """

        with self._lock:
            if serial_port is not None:
                self._config.serial_port = serial_port.strip() or self._config.serial_port
                serial_link_manager.configure_port(self._config.serial_port)
            if wifi_ssid is not None:
                self._config.wifi_ssid = wifi_ssid.strip()
            if wifi_password is not None:
                self._config.wifi_password = wifi_password
            if server_ip is not None:
                self._config.server_ip = server_ip.strip()
            if server_port is not None:
                self._config.server_port = int(server_port)
            if wifi_connect_timeout_ms is not None:
                self._config.wifi_connect_timeout_ms = int(wifi_connect_timeout_ms)
            if tcp_connect_timeout_ms is not None:
                self._config.tcp_connect_timeout_ms = int(tcp_connect_timeout_ms)
            if keepalive_period_ms is not None:
                self._config.keepalive_period_ms = int(keepalive_period_ms)
            self._append_log(
                "Updated transport config: "
                f"port={self._config.serial_port}, ssid={self._config.wifi_ssid}, "
                f"server={self._config.server_ip}:{self._config.server_port}."
            )
            return self._snapshot_locked()

    def open_transport(self, port_name: str | None = None) -> LinkSnapshot:
        with self._lock:
            if port_name:
                self._config.serial_port = port_name.strip() or self._config.serial_port
                serial_link_manager.configure_port(self._config.serial_port)
            target_port = self._config.serial_port
            transport_snapshot = serial_link_manager.get_snapshot()
            if transport_snapshot.port_open and transport_snapshot.port_name == target_port:
                self._append_log(f"Serial transport already open on {target_port}; open request ignored.")
                return self._snapshot_locked()

        try:
            serial_link_manager.open_port()
        except RuntimeError:
            with self._lock:
                self._bridge_ready = False
                self._set_error("COM port not found")
                return self._snapshot_locked()

        with self._lock:
            self._bridge_ready = True
            self._append_log("Serial transport opened.")
            return self._snapshot_locked()

    def close_transport(self) -> LinkSnapshot:
        with self._lock:
            serial_link_manager.close_port()
            self._clear_runtime_flow_locked()
            self._bridge_ready = False
            self._wifi_connected = False
            self._tcp_connected = False
            self._append_log("Serial transport closed.")
            return self._snapshot_locked()

    def reset(self) -> LinkSnapshot:
        """@brief Stop transmission, clear buffers, and reset the ESP controller.

        @details Reset is the only permitted recovery path from `error`. It
        clears all buffered transport state, drops active supervision, and
        re-initializes the low-level controller with a RESET frame.
        """

        with self._lock:
            serial_link_manager.clear_buffers()
            self._host_live_integer = 0
            self._device_live_integer = 0
            self._clear_runtime_flow_locked()
            self._wifi_ready = False
            self._wifi_connected = False
            self._tcp_connected = False
            self._bridge_ready = False
            self._set_state(LinkState.RESET, "Server reset issued. Transmission stopped and buffers cleared.")
            if not self._try_send_command_locked(
                MessageType.RESET,
                "reset",
                "reset transmit failed",
            ):
                return self._snapshot_locked()
            self._pending_auto_stage = LinkState.INITIALIZE
            return self._snapshot_locked()

    def initialize(self) -> LinkSnapshot:
        """@brief Load the ESP controller and automatically enter connect wait.

        @details Initialize validates the mirrored transport constants, sends
        them to the controller, immediately issues the low-level connect
        request, and then waits for a connect response before entering the
        keepalive state.
        """

        with self._lock:
            self._poll_received_frames_locked()
            if self._current_state is LinkState.ERROR:
                self._append_log("Initialize ignored because reset is required to clear the latched error.")
                return self._snapshot_locked()
            self._clear_runtime_flow_locked()
            self._begin_initialize_locked("Initialize command started automatic controller loading.")
            return self._snapshot_locked()

    def send_keepalive(self) -> LinkSnapshot:
        """@brief Drive the active keepalive state and supervise the response.

        @details Keepalive is only valid once connect has succeeded. A missing
        response or stalled transport advances the runtime to `error`.
        """

        with self._lock:
            self._poll_received_frames_locked()
            if self._current_state is LinkState.ERROR:
                self._append_log("Keepalive ignored because reset is required to clear the latched error.")
                return self._snapshot_locked()
            if not self._initialize_completed or not self._connect_completed:
                self._set_error("keepalive invoke rejected: server has not completed initialize and connect")
                return self._snapshot_locked()
            if self._current_state not in (LinkState.KEEPALIVE, LinkState.SEND_DATA):
                self._set_error("keepalive invoke rejected: active keepalive state is not available")
                return self._snapshot_locked()
            self._host_live_integer += 1
            if not self._try_send_command_locked(
                MessageType.KEEPALIVE,
                "keepalive",
                "keepalive transmit failed",
            ):
                return self._snapshot_locked()
            self._last_keepalive_tx = datetime.now(UTC)
            self._keepalive_ack_pending = True
            self._watchdog_armed = True
            if self._current_state is not LinkState.KEEPALIVE:
                self._set_state(LinkState.KEEPALIVE, "Keepalive supervision resumed.")
            return self._snapshot_locked()

    def send_data(self, payload_text: str = "espresso_payload") -> LinkSnapshot:
        """@brief Send application data only after keepalive is active.

        @details The send-data command remains disabled until the connect phase
        succeeds. The runtime stays in `send_data` while data traffic is
        allowed, but error recovery still requires `reset`.

        @param[in] payload_text UTF-8 payload text to transmit in the DATA frame.
        """

        with self._lock:
            self._poll_received_frames_locked()
            if self._current_state is LinkState.ERROR:
                self._append_log("Send data ignored because reset is required to clear the latched error.")
                return self._snapshot_locked()
            if not self._initialize_completed or not self._connect_completed:
                self._set_error("send data invoke rejected: server has not completed initialize and connect")
                return self._snapshot_locked()
            if not self._send_data_enabled or self._current_state not in (LinkState.KEEPALIVE, LinkState.SEND_DATA):
                self._set_error("send data invoke rejected: keepalive-ready connection is not available")
                return self._snapshot_locked()
            if not self._try_send_command_locked(
                MessageType.DATA,
                payload_text,
                "send data transmit failed",
            ):
                return self._snapshot_locked()
            self._set_state(LinkState.SEND_DATA, "Send-data command issued.")
            return self._snapshot_locked()

    def get_snapshot(self) -> LinkSnapshot:
        with self._lock:
            self._poll_received_frames_locked()
            self._advance_automatic_flow_locked()
            self._evaluate_watchdog_locked()
            return self._snapshot_locked()

    def _snapshot_locked(self) -> LinkSnapshot:
        transport_snapshot: SerialLinkSnapshot = serial_link_manager.get_snapshot()
        important_data = {
            "Current State": self._current_state.value,
            "Serial Port": self._config.serial_port,
            "Port Open": "Yes" if transport_snapshot.port_open else "No",
            "Wi-Fi SSID": self._config.wifi_ssid,
            "Server Endpoint": f"{self._config.server_ip}:{self._config.server_port}",
            "Wi-Fi Ready": "Yes" if self._wifi_ready else "No",
            "Bridge Ready": "Yes" if self._bridge_ready else "No",
            "Initialize Passed": "Yes" if self._initialize_completed else "No",
            "Connect Passed": "Yes" if self._connect_completed else "No",
            "Wi-Fi Connected": "Yes" if self._wifi_connected else "No",
            "TCP Connected": "Yes" if self._tcp_connected else "No",
            "Send Data Enabled": "Yes" if self._send_data_enabled else "No",
            "Wi-Fi Timeout (ms)": str(self._config.wifi_connect_timeout_ms),
            "TCP Timeout (ms)": str(self._config.tcp_connect_timeout_ms),
            "Keepalive Period (ms)": str(self._config.keepalive_period_ms),
            "HostLiveInteger": str(self._host_live_integer),
            "DeviceLiveInteger": str(self._device_live_integer),
            "Sequence": str(self._sequence),
            "Watchdog Armed": "Yes" if self._watchdog_armed else "No",
            "Last Transition": self._last_transition_at,
            "Last Monitor Event": self._last_monitor_event,
            "Last Monitor At": self._last_monitor_at,
            "Last RX": transport_snapshot.last_rx_at,
            "Last TX": transport_snapshot.last_tx_at,
            "Last Error": self._last_error or transport_snapshot.last_error or "None",
        }
        return LinkSnapshot(
            current_state=self._current_state.value,
            serial_port=self._config.serial_port,
            config=asdict(self._config),
            last_transition_at=self._last_transition_at,
            watchdog_armed=self._watchdog_armed,
            host_live_integer=self._host_live_integer,
            device_live_integer=self._device_live_integer,
            sequence=self._sequence,
            last_error=self._last_error or transport_snapshot.last_error,
            important_data=important_data,
            transport=asdict(transport_snapshot),
            logs=self._combined_logs_locked(),
            available_states=[state.value for state in LinkState],
        )


link_runtime = LinkRuntime()
