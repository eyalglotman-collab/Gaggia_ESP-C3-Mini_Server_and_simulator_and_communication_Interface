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
    WAIT_FOR_COM_RESET = "wait_for_com_reset"


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
    wifi_enabled: bool
    config: dict[str, object]
    last_transition_at: str
    watchdog_armed: bool
    connection_fault: bool
    server_live_integer: int
    client_live_integer: int
    consecutive_keepalive_failures: int
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
        self._server_live_integer = 0
        self._client_live_integer = 0
        self._connection_fault = False
        self._consecutive_keepalive_failures = 0
        self._sequence = 0
        self._watchdog_armed = False
        self._pending_auto_stage: LinkState | None = None
        self._last_error = ""
        self._last_monitor_event = "Simulator monitor ready."
        self._last_monitor_at = self._timestamp()
        self._last_snapshot_poll_at: datetime | None = None
        self._wifi_enabled = True
        self._wifi_ready = False
        self._wifi_connected = False
        self._tcp_connected = False
        self._bridge_ready = False
        self._initialize_completed = False
        self._connect_completed = False
        self._last_received_client_text = "No client text received yet."
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
        self._initialize_completed = False
        self._connect_completed = False
        self._pending_auto_stage = None
        self._last_received_client_text = "No client text received yet."

    def _clear_connection_fault_locked(self) -> None:
        """@brief Clear the connection-fault latch after a valid keepalive exchange.

        @details The simulator keeps the same sequential-failure behavior as the
        client. One successful keepalive proves both sides are synchronized
        again, so the retry counter and the latched operator fault are cleared.
        """

        self._connection_fault = False
        self._consecutive_keepalive_failures = 0

    def _set_runtime_fault_locked(self, message: str) -> None:
        """@brief Latch a blocking connection fault and wait for explicit reset.

        @details Runtime faults that the operator must inspect drive the
        simulator into `wait_for_com_reset` instead of an `error` state so the
        server lifecycle matches the current client transport design.
        """

        self._clear_runtime_flow_locked()
        self._tcp_connected = False
        self._wifi_connected = False
        self._connection_fault = True
        self._last_error = message
        self._set_state(LinkState.WAIT_FOR_COM_RESET, "Connection fault latched. Waiting for Reset Communication.", message)

    def _schedule_reconnect_locked(self, message: str, *, keepalive_failure: bool) -> None:
        """@brief Retry through `connect` or stop in `wait_for_com_reset`.

        @details The simulator retries low-level link loss automatically until
        five sequential keepalive failures have been observed. After that it
        latches `ConnectionFault` and waits for an explicit reset action.
        """

        self._clear_runtime_flow_locked()
        self._tcp_connected = False
        self._wifi_connected = False
        self._last_error = message

        if keepalive_failure:
            self._consecutive_keepalive_failures += 1
            self._append_log(
                "Keepalive failure recorded "
                f"({self._consecutive_keepalive_failures}/5): {message}"
            )
            if self._consecutive_keepalive_failures >= 5:
                self._set_runtime_fault_locked(message)
                return
        if self._current_state is LinkState.WAIT_FOR_COM_RESET:
            return
        self._set_state(LinkState.CONNECT, "Transport retry scheduled through connect.", message)
        self._wifi_connected = False
        self._tcp_connected = False

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
            self._set_runtime_fault_locked(f"{failure_prefix}: {exc}")
            return False
        return True

    def _begin_initialize_locked(self, reason: str) -> None:
        """@brief Start the initialize stage of the automatic controller flow.

        @details This stage validates configuration, loads the ESP controller
        with mirrored constants, and schedules the automatic connect step.
        """

        config_error = self._validate_config_locked()
        if config_error is not None:
            self._set_runtime_fault_locked(config_error)
            return
        if not serial_link_manager.get_snapshot().port_open:
            self._set_runtime_fault_locked("COM port not found")
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

        @details Connect is now a monitor-only state on the Python side. The
        ESP32-C3 bridge owns the real TCP listener and low-level handshake with
        the client, so the host no longer sends a duplicate `CONNECT` command
        over USB. Instead, the host enters `connect` and waits for the bridge
        to mirror `tcp_connected`/`client_connected` progress.
        """

        if not self._wifi_ready:
            self._set_runtime_fault_locked("initialize did not complete before connect")
            return
        self._pending_auto_stage = None
        self._initialize_completed = True
        self._wifi_connected = False
        self._tcp_connected = False
        self._set_state(LinkState.CONNECT, reason)

    def _advance_automatic_flow_locked(self) -> None:
        """@brief Progress the automatic reset-to-keepalive sequence.

        @details The runtime advances one stage per snapshot poll so the UI can
        observe reset, initialize, and connect as distinct server states while
        the ESP32-C3 bridge remains the sole owner of the real TCP handshake.
        """

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
            if category != "poll":
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
            self._set_runtime_fault_locked(f"{context}: {detail or 'generic unknown failure'}")
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
        bridge through the low-level initialize/connect sequence. Runtime
        keepalive/watchdog ownership itself lives on the bridge firmware.
        """

        if not self._config.serial_port.strip():
            return "COM port not found"
        if not self._wifi_enabled:
            return "Wi-Fi is disabled"
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

        frames = serial_link_manager.pop_received_frames()
        if not frames:
            return

        reconnect_scheduled = False

        for frame in frames:
            self._server_live_integer = frame.host_live_integer
            self._client_live_integer = frame.device_live_integer
            self._append_log(
                f"Received {frame.message_type.name} seq={frame.sequence} "
                f"server={frame.host_live_integer} client={frame.device_live_integer}."
            )
            payload_text = frame.payload.decode("utf-8", errors="ignore").strip().lower()

            if frame.message_type == MessageType.ERROR:
                self._schedule_reconnect_locked(
                    payload_text or "generic unknown failure",
                    keepalive_failure=(payload_text == "keepalive_supervision_lost"),
                )
                reconnect_scheduled = True
                break

            if frame.message_type in (MessageType.KEEPALIVE, MessageType.DATA):
                self._watchdog_armed = True
                self._wifi_connected = True
                self._tcp_connected = True
                self._bridge_ready = True
                self._clear_connection_fault_locked()
                if self._current_state is LinkState.CONNECT:
                    self._pending_auto_stage = None
                    self._connect_completed = True
                    self._set_state(
                        LinkState.KEEPALIVE,
                        "Mirrored bridge keepalive/data traffic confirmed the TCP session. Server state advanced to keepalive.",
                    )
                    if frame.message_type == MessageType.DATA:
                        self._last_received_client_text = (
                            frame.payload.decode("utf-8", errors="replace") or "Empty client payload"
                        )
                    continue

            if self._current_state is LinkState.CONNECT and payload_text == "connect_ack":
                self._wifi_connected = True
                self._tcp_connected = False
                self._bridge_ready = True
                self._append_log(
                    "Bridge acknowledged CONNECT, but the runtime is still waiting for explicit client connection success."
                )
                continue

            if self._current_state is LinkState.CONNECT and payload_text in CONNECT_SUCCESS_PAYLOADS:
                self._pending_auto_stage = None
                self._connect_completed = True
                # Do not arm keepalive supervision until the first real
                # keepalive/data exchange occurs. Connect success alone only
                # proves the session exists, not that keepalive traffic has
                # already started.
                self._watchdog_armed = False
                self._wifi_connected = True
                self._tcp_connected = True
                self._bridge_ready = True
                self._set_state(
                    LinkState.KEEPALIVE,
                    "Explicit client connection success received. Server state advanced to keepalive ready.",
                )
                continue

            if self._current_state is LinkState.KEEPALIVE:
                if frame.message_type == MessageType.DATA:
                    self._last_received_client_text = frame.payload.decode("utf-8", errors="replace") or "Empty client payload"

        if reconnect_scheduled:
            serial_link_manager.clear_buffers()

    def _send_command_locked(self, message_type: MessageType, payload_text: str = "") -> None:
        frame = Frame(
            message_type=message_type,
            host_live_integer=self._server_live_integer,
            device_live_integer=self._client_live_integer,
            sequence=self._next_sequence(),
            payload=payload_text.encode("utf-8"),
        )
        serial_link_manager.send_frame(frame)
        self._append_log(f"Sent {message_type.name} seq={frame.sequence} server={frame.host_live_integer}.")

    def _sync_bridge_wifi_state_locked(self) -> None:
        """@brief Push the simulator Wi-Fi state down to the ESP32-C3 bridge.

        @details Backend restarts reset the host-side `wifi_enabled` flag to its
        default, but the bridge may still hold the previous runtime state. This
        sync step makes the physical SoftAP state deterministic whenever the COM
        port is opened or otherwise re-synchronized.
        """

        transport_snapshot = serial_link_manager.get_snapshot()
        if not transport_snapshot.port_open:
            return

        self._send_command_locked(
            MessageType.DATA,
            "wifi_enable" if self._wifi_enabled else "wifi_disable",
        )
        self._append_log(
            "Synchronized bridge Wi-Fi state to "
            + ("enabled." if self._wifi_enabled else "disabled.")
        )

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
        """@brief Open the COM port and force a clean bridge resynchronization.

        @details Opening the simulator onto an already-active bridge session
        can expose stale keepalive/error frames from the previous runtime. The
        open path therefore clears serial buffers and immediately issues a
        low-level RESET so the host always starts from a known transport state.
        """

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
                self._set_runtime_fault_locked("COM port not found")
                return self._snapshot_locked()

        with self._lock:
            self._bridge_ready = True
            self._append_log("Serial transport opened. Forcing clean bridge reset before accepting transport traffic.")

        serial_link_manager.clear_buffers()
        return self.reset()

    def close_transport(self) -> LinkSnapshot:
        with self._lock:
            serial_link_manager.close_port()
            self._clear_runtime_flow_locked()
            self._bridge_ready = False
            self._wifi_connected = False
            self._tcp_connected = False
            self._append_log("Serial transport closed.")
            return self._snapshot_locked()

    def force_release_transport(self) -> LinkSnapshot:
        """@brief Force-release the configured COM port from all likely holders.

        @details Closes the simulator-owned handle first, then hard-stops
        external processes that match the configured COM port and known serial
        tooling patterns.
        """

        with self._lock:
            snapshot, released_pids = serial_link_manager.force_release_port()
            self._clear_runtime_flow_locked()
            self._bridge_ready = False
            self._wifi_ready = False
            self._wifi_connected = False
            self._tcp_connected = False
            self._clear_connection_fault_locked()
            self._set_state(LinkState.RESET, "Hard COM release executed. Communication returned to reset idle.")
            detail = ", ".join(str(pid) for pid in released_pids) if released_pids else "none"
            self._append_log(f"Release COM Port terminated external PIDs: {detail}.")
            return self._snapshot_locked()

    def toggle_wifi_enabled(self) -> LinkSnapshot:
        """@brief Toggle the low-level Wi-Fi availability flag for testing.

        @details This does not change the saved SSID/password values. It simply
        simulates whether the transport runtime is allowed to proceed into the
        Wi-Fi/TCP phases during the automatic reset-to-keepalive flow.
        """

        with self._lock:
            self._wifi_enabled = not self._wifi_enabled
            transport_snapshot = serial_link_manager.get_snapshot()
            if transport_snapshot.port_open:
                try:
                    self._send_command_locked(
                        MessageType.DATA,
                        "wifi_enable" if self._wifi_enabled else "wifi_disable",
                    )
                except RuntimeError as exc:
                    self._append_log(f"Wi-Fi bridge control command failed: {exc}")
            self._clear_runtime_flow_locked()
            self._wifi_ready = False
            self._wifi_connected = False
            self._tcp_connected = False
            self._bridge_ready = transport_snapshot.port_open
            self._clear_connection_fault_locked()
            self._set_state(
                LinkState.RESET,
                "Low-level bridge Wi-Fi enabled."
                if self._wifi_enabled
                else "Low-level bridge Wi-Fi disabled.",
            )
            return self._snapshot_locked()

    def reset(self) -> LinkSnapshot:
        """@brief Stop transmission, clear buffers, and reset the ESP controller.

        @details Reset clears all buffered transport state, drops active
        supervision, clears `ConnectionFault`, and re-initializes the low-level
        controller with a RESET frame.
        """

        with self._lock:
            serial_link_manager.clear_buffers()
            self._server_live_integer = 0
            self._client_live_integer = 0
            self._clear_runtime_flow_locked()
            self._wifi_ready = False
            self._wifi_connected = False
            self._tcp_connected = False
            self._bridge_ready = False
            self._clear_connection_fault_locked()
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
            if self._current_state is LinkState.WAIT_FOR_COM_RESET:
                self._append_log("Initialize ignored because Reset Communication is required to clear the latched fault.")
                return self._snapshot_locked()
            self._clear_runtime_flow_locked()
            self._begin_initialize_locked("Initialize command started automatic controller loading.")
            return self._snapshot_locked()

    def send_keepalive(self) -> LinkSnapshot:
        """@brief Return the current snapshot for the bridge-owned keepalive path.

        @details The ESP32-C3 bridge now owns keepalive initiation against the
        client TCP session, so the simulator host no longer emits keepalive
        frames directly over USB.
        """

        with self._lock:
            self._poll_received_frames_locked()
            self._append_log("Keepalive command surface is monitor-only; the bridge owns keepalive initiation.")
            return self._snapshot_locked()

    def send_data(self, payload_text: str = "espresso_payload") -> LinkSnapshot:
        """@brief Send application data only after keepalive is active.

        @details The send-data command remains disabled until the keepalive
        phase has been proven. The server runtime stays in `keepalive` while
        data traffic is allowed so the lifecycle remains canonical with the
        client transport design.

        @param[in] payload_text UTF-8 payload text to transmit in the DATA frame.
        """

        with self._lock:
            self._poll_received_frames_locked()
            if self._current_state is LinkState.WAIT_FOR_COM_RESET:
                self._append_log("Send data ignored because Reset Communication is required to clear the latched fault.")
                return self._snapshot_locked()
            if not self._initialize_completed or not self._connect_completed:
                self._set_runtime_fault_locked("send data invoke rejected: server has not completed initialize and connect")
                return self._snapshot_locked()
            if self._current_state is not LinkState.KEEPALIVE:
                self._set_runtime_fault_locked("send data invoke rejected: keepalive-ready connection is not available")
                return self._snapshot_locked()
            if not self._try_send_command_locked(
                MessageType.DATA,
                payload_text,
                "send data transmit failed",
            ):
                return self._snapshot_locked()
            self._set_state(LinkState.KEEPALIVE, "Send-data command issued during keepalive.")
            return self._snapshot_locked()

    def get_snapshot(self) -> LinkSnapshot:
        with self._lock:
            self._poll_received_frames_locked()
            self._advance_automatic_flow_locked()
            return self._snapshot_locked()

    def _snapshot_locked(self) -> LinkSnapshot:
        transport_snapshot: SerialLinkSnapshot = serial_link_manager.get_snapshot()
        important_data = {
            "Current State": self._current_state.value,
            "Serial Port": self._config.serial_port,
            "Port Open": "Yes" if transport_snapshot.port_open else "No",
            "Wi-Fi Enabled": "Yes" if self._wifi_enabled else "No",
            "Wi-Fi SSID": self._config.wifi_ssid,
            "Server Endpoint": f"{self._config.server_ip}:{self._config.server_port}",
            "Wi-Fi Ready": "Yes" if self._wifi_ready else "No",
            "Bridge Ready": "Yes" if self._bridge_ready else "No",
            "Initialize Passed": "Yes" if self._initialize_completed else "No",
            "Connect Passed": "Yes" if self._connect_completed else "No",
            "Wi-Fi Connected": "Yes" if self._wifi_connected else "No",
            "TCP Connected": "Yes" if self._tcp_connected else "No",
            "ConnectionFault": "Yes" if self._connection_fault else "No",
            "Keepalive Failures": str(self._consecutive_keepalive_failures),
            "Text Received From Client": self._last_received_client_text,
            "Wi-Fi Timeout (ms)": str(self._config.wifi_connect_timeout_ms),
            "TCP Timeout (ms)": str(self._config.tcp_connect_timeout_ms),
            "Keepalive Period (ms)": str(self._config.keepalive_period_ms),
            "ServerLiveInteger": str(self._server_live_integer),
            "ClientLiveInteger": str(self._client_live_integer),
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
            wifi_enabled=self._wifi_enabled,
            config=asdict(self._config),
            last_transition_at=self._last_transition_at,
            watchdog_armed=self._watchdog_armed,
            connection_fault=self._connection_fault,
            server_live_integer=self._server_live_integer,
            client_live_integer=self._client_live_integer,
            consecutive_keepalive_failures=self._consecutive_keepalive_failures,
            sequence=self._sequence,
            last_error=self._last_error or transport_snapshot.last_error,
            important_data=important_data,
            transport=asdict(transport_snapshot),
            logs=self._combined_logs_locked(),
            available_states=[state.value for state in LinkState],
        )


link_runtime = LinkRuntime()
