"""Low-level transport state machine for the simulator template."""

from __future__ import annotations

import traceback
from collections import deque
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from threading import Lock
from time import monotonic, sleep
from traceback import format_exception_only

from server.communication.data_structures_lcd_controller import (
    LCDControllerProfileSummary,
    build_profile_summaries_from_presets,
)
from server.communication.protocol_lcd_controller import LCDControllerProtocolBridge
from server.transport.frame_codec import Frame, MessageType
from server.transport.serial_link import SerialLinkSnapshot, serial_link_manager
from server.sim.data_payload import DATA_MAGIC_UPLINK, data_payload_manager

WATCHDOG_MS = 100
WATCHDOG_GRACE_MS = 400
RUNNING_INTEGER_TIMEOUT_MS = 400
SHOT_DONE_SUCCESS_DELAY_SEC = 3.0
BOTTOM_LAYER_RETRY_LIMIT = 3
TOP_LAYER_FAILURE_LIMIT = 3
CONNECT_SUCCESS_PAYLOADS = {"client_connected", "connect_success", "tcp_connected"}
SERIAL_OPEN_RETRY_LIMIT = 4
SERIAL_OPEN_RETRY_BASE_BACKOFF_S = 0.2
SERIAL_OPEN_TRANSIENT_ERROR_TOKENS = (
    "semaphore timeout",
    "cannot configure port",
    "access is denied",
    "device not functioning",
    "i/o error",
    "resource busy",
    "permission denied",
    "clearcommerror failed",
)
SERIAL_OPEN_FORCE_RELEASE_ERROR_TOKENS = (
    "semaphore timeout",
    "cannot configure port",
    "access is denied",
    "permission denied",
    "resource busy",
)


class LinkState(StrEnum):
    RESET = "reset"
    INITIALIZE = "initialize"
    CONNECT = "connect"
    KEEPALIVE_SERVER_SEND = "keepalive_server_send"
    KEEPALIVE_CLIENT_RETURN = "keepalive_client_return"
    DISCONNECT = "disconnect"
    ERROR = "error"


class DataSimulationEvent(StrEnum):
    """@brief Client-issued data simulation control events."""

    ON = "datasimulationon"
    OFF = "datasimulationoff"
    START_BREW = "startbrew"
    STOP_BREW = "stopbrew"
    PROFILE_SELECTION = "profileselection"


STATE_DISPLAY_NAMES = {
    LinkState.RESET: "Reset",
    LinkState.INITIALIZE: "Initialize",
    LinkState.CONNECT: "Connect",
    LinkState.KEEPALIVE_SERVER_SEND: "USB_TransportKeepAlive_ServerSend",
    LinkState.KEEPALIVE_CLIENT_RETURN: "USB_TransportKeepAlive_ClientReturn",
    LinkState.DISCONNECT: "Disconnect",
    LinkState.ERROR: "Error",
}


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
    bottom_layer_retry_limit: int = BOTTOM_LAYER_RETRY_LIMIT
    top_layer_failure_limit: int = TOP_LAYER_FAILURE_LIMIT


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
    bottom_layer_retry_count: int
    top_layer_failure_count: int
    top_layer_connect_streak: int
    bottom_layer_checksum_error_count: int
    bottom_layer_sequence_error_count: int
    sequence: int
    transport_last_delay_ms: int
    transport_max_delay_ms: int
    total_error_count: int
    esp32c3_connected: bool
    esp32c3_link_state: str
    usb_rx_kbytes_per_sec: float
    usb_tx_kbytes_per_sec: float
    usb_total_kbytes_per_sec: float
    last_error: str
    important_data: dict[str, str]
    telemetry_data: dict[str, str]
    profile_dataset: dict[str, object]
    transport: dict[str, object]
    logs: list[str]
    low_level_logs: list[str]
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
        self._bottom_layer_retry_count = 0
        self._top_layer_failure_count = 0
        self._top_layer_connect_streak = 0
        self._bottom_layer_checksum_error_count = 0
        self._bottom_layer_sequence_error_count = 0
        self._last_peer_sequence: int | None = None
        self._sequence = 0
        self._transport_last_delay_ms = 0
        self._transport_max_delay_ms = 0
        self._total_error_count = 0
        self._firmware_error_telemetry_seen = False
        self._firmware_delay_telemetry_missing_warned = False
        self._watchdog_armed = False
        self._running_integer_seeded = False
        self._last_running_integer_rx_at: datetime | None = None
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
        self._last_brew_active = False
        self._shot_done_pending = False
        self._shot_done_due_monotonic = 0.0
        # --- Enhanced telemetry counters ---
        self._session_started_at: datetime | None = None
        self._keepalive_req_count: int = 0
        self._keepalive_resp_count: int = 0
        self._data_frames_rx_count: int = 0
        self._data_frames_tx_count: int = 0
        self._transport_min_delay_ms: int = 0
        self._ka_timing_valid: bool = False
        self._watchdog_timeout_count: int = 0
        # USB throughput is derived from pyserial transport byte counters.
        self._usb_rx_kbytes_per_sec: float = 0.0
        self._usb_tx_kbytes_per_sec: float = 0.0
        self._usb_total_kbytes_per_sec: float = 0.0
        self._usb_last_sample_monotonic: float | None = None
        self._usb_last_rx_bytes: int = 0
        self._usb_last_tx_bytes: int = 0
        self._lcd_protocol_bridge = LCDControllerProtocolBridge()
        self._lcd_protocol_bridge.initialize_hooks(
            send_payload_callback=self._send_lcd_protocol_payload_locked,
            profile_provider_callback=self._get_lcd_protocol_profiles,
            dataset_provider_callback=self._get_lcd_protocol_dataset,
        )
        self._append_log("Transport runtime ready. Default TopLayer state is reset.")

    def _timestamp(self) -> str:
        now = datetime.now(UTC)
        return f"{now:%H:%M:%S}.{now.microsecond // 10000:02d}"

    def _append_log(self, message: str) -> None:
        self._logs.appendleft(f"[{self._timestamp()}] {message}")

    def _state_display_name(self, state: LinkState) -> str:
        """@brief Convert one runtime enum into the canonical ESP32-C label.

        @details The bridge firmware logs state names in PascalCase (for
        example `InterimDebug`). The simulator UI reuses this helper so all
        state indicators match the firmware naming exactly.
        """

        return STATE_DISPLAY_NAMES.get(state, state.value)

    def _combined_logs_locked(self) -> list[str]:
        """@brief Return the UI logger feed in newest-to-oldest order.

        @details Both runtime logs and serial-manager logs are stored with the
        newest entry first. The UI logger is capped to the last 2000 messages
        across both sources so the browser keeps a bounded rolling history.
        """

        combined_logs = list(self._logs) + serial_link_manager.get_logs()
        combined_logs.sort(reverse=True)
        return combined_logs[:2000]

    def _update_usb_throughput_locked(self, transport_snapshot: SerialLinkSnapshot) -> None:
        """@brief Update USB throughput metrics from pyserial byte telemetry.

        @details The serial transport manager already tracks cumulative RX/TX
        bytes. This helper samples those counters on every snapshot and computes
        kB/s rates using a monotonic clock so wall-clock jumps do not skew
        throughput calculations.
        """

        now = monotonic()
        current_rx_bytes = int(transport_snapshot.rx_bytes)
        current_tx_bytes = int(transport_snapshot.tx_bytes)

        if self._usb_last_sample_monotonic is None:
            self._usb_last_sample_monotonic = now
            self._usb_last_rx_bytes = current_rx_bytes
            self._usb_last_tx_bytes = current_tx_bytes
            self._usb_rx_kbytes_per_sec = 0.0
            self._usb_tx_kbytes_per_sec = 0.0
            self._usb_total_kbytes_per_sec = 0.0
            return

        delta_seconds = now - self._usb_last_sample_monotonic
        if delta_seconds <= 0.0:
            self._usb_last_sample_monotonic = now
            self._usb_last_rx_bytes = current_rx_bytes
            self._usb_last_tx_bytes = current_tx_bytes
            return

        delta_rx_bytes = current_rx_bytes - self._usb_last_rx_bytes
        delta_tx_bytes = current_tx_bytes - self._usb_last_tx_bytes
        if delta_rx_bytes < 0:
            delta_rx_bytes = 0
        if delta_tx_bytes < 0:
            delta_tx_bytes = 0

        self._usb_rx_kbytes_per_sec = (delta_rx_bytes / 1024.0) / delta_seconds
        self._usb_tx_kbytes_per_sec = (delta_tx_bytes / 1024.0) / delta_seconds
        self._usb_total_kbytes_per_sec = self._usb_rx_kbytes_per_sec + self._usb_tx_kbytes_per_sec

        self._usb_last_sample_monotonic = now
        self._usb_last_rx_bytes = current_rx_bytes
        self._usb_last_tx_bytes = current_tx_bytes

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
        self._top_layer_connect_streak = 0
        self._last_peer_sequence = None
        self._running_integer_seeded = False
        self._last_running_integer_rx_at = None
        self._last_received_client_text = "No client text received yet."
        self._last_brew_active = False
        self._shot_done_pending = False
        self._shot_done_due_monotonic = 0.0

    def _clear_connection_fault_locked(self) -> None:
        """@brief Clear the connection-fault latch after a valid keepalive exchange.

        @details The simulator keeps the same sequential-failure behavior as the
        client. One successful keepalive proves both sides are synchronized
        again, so the retry counter and the latched operator fault are cleared.
        """

        self._connection_fault = False
        self._consecutive_keepalive_failures = 0
        self._top_layer_failure_count = 0

    def _record_total_error_locked(self, reason_text: str) -> None:
        """@brief Increment the aggregate telemetry counter for any error event.

        @details The total error counter intentionally includes both low-level
        retry errors and high-level protocol failures so operators can monitor
        overall communication instability from a single number. Once firmware
        telemetry is available, the simulator defers to the ESP32-C3-provided
        total error counter and stops host-side increments.
        """

        del reason_text
        if self._firmware_error_telemetry_seen:
            return
        self._total_error_count += 1

    def _ingest_firmware_telemetry_from_payload_locked(self, payload_text: str) -> None:
        """@brief Update simulator telemetry from ESP32-C3 payload metadata.

        @details The bridge embeds firmware-owned telemetry inside KEEPALIVE
        payload text (`td_last_ms`, `td_max_ms`, `terr`). When those fields are
        present, they become the authoritative values displayed in the UI.
        """

        delay_last = self._try_parse_i32_payload_value(payload_text, "td_last_ms")
        delay_max = self._try_parse_u32_payload_value(payload_text, "td_max_ms")
        total_errors = self._try_parse_u32_payload_value(payload_text, "terr")

        if delay_last is not None:
            self._transport_last_delay_ms = delay_last
            if delay_last >= 0:
                if not self._ka_timing_valid or delay_last < self._transport_min_delay_ms:
                    self._transport_min_delay_ms = delay_last
                self._ka_timing_valid = True
        if delay_max is not None:
            self._transport_max_delay_ms = delay_max
        if delay_last is None and delay_max is None and payload_text.startswith("ka_"):
            if not self._firmware_delay_telemetry_missing_warned:
                self._append_log(
                    "Controller telemetry missing `td_last_ms`/`td_max_ms`; flash ESP32-C3 bridge firmware to enable controller-level delay values."
                )
                self._firmware_delay_telemetry_missing_warned = True
        elif delay_last is not None or delay_max is not None:
            self._firmware_delay_telemetry_missing_warned = False
        if total_errors is not None:
            self._total_error_count = total_errors
            self._firmware_error_telemetry_seen = True

    def _set_runtime_fault_locked(self, message: str, *, count_total_error: bool = True) -> None:
        """@brief Latch a blocking TopLayer fault and require explicit reset."""

        if count_total_error:
            self._record_total_error_locked(message)

        self._clear_runtime_flow_locked()
        self._tcp_connected = False
        self._wifi_connected = False
        self._connection_fault = True
        self._last_error = message
        self._set_state(LinkState.ERROR, "TopLayer fault latched. Reset is required.", message)

    def _record_top_layer_failure_locked(self, message: str) -> None:
        """@brief Record a TopLayer failure and escalate to error at threshold."""

        self._record_total_error_locked(message)
        self._top_layer_failure_count += 1
        self._consecutive_keepalive_failures += 1
        self._append_log(
            f"TopLayer failure ({self._top_layer_failure_count}/{TOP_LAYER_FAILURE_LIMIT}): {message}"
        )
        if self._top_layer_failure_count >= TOP_LAYER_FAILURE_LIMIT:
            self._set_runtime_fault_locked(message, count_total_error=False)
            return
        self._clear_runtime_flow_locked()
        self._tcp_connected = False
        self._wifi_connected = False
        self._set_state(LinkState.RESET, "TopLayer failure routed to reset before reconnect.", message)

    def _schedule_bottom_layer_retry_locked(self, message: str, *, checksum_failure: bool) -> None:
        """@brief Retry BottomLayer connectivity and escalate when retries exhaust."""

        self._record_total_error_locked(message)
        self._bottom_layer_retry_count += 1
        if checksum_failure:
            self._bottom_layer_checksum_error_count += 1
        self._append_log(
            f"BottomLayer retry ({self._bottom_layer_retry_count}/{BOTTOM_LAYER_RETRY_LIMIT}): {message}"
        )
        if self._bottom_layer_retry_count >= BOTTOM_LAYER_RETRY_LIMIT:
            self._record_top_layer_failure_locked("BottomLayer retries exhausted")
            return

        self._clear_runtime_flow_locked()
        self._tcp_connected = False
        self._wifi_connected = False
        self._set_state(LinkState.CONNECT, "BottomLayer retry scheduled through connect.", message)

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

        print(f"\n[link-runtime] INTERNAL FAILURE in {context}: {exc}", flush=True)
        traceback.print_exc()
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

    @staticmethod
    def _is_transient_serial_open_error(error_text: str) -> bool:
        """@brief Classify known transient Windows serial-open failures."""

        normalized = (error_text or "").strip().lower()
        if not normalized:
            return False
        return any(token in normalized for token in SERIAL_OPEN_TRANSIENT_ERROR_TOKENS)

    @staticmethod
    def _should_force_release_on_open_error(error_text: str) -> bool:
        """@brief Return whether a COM open fault should trigger hard release.

        @details Some Windows COM failures (for example access denied) are
        caused by stale external holders. Force-release before retry helps the
        simulator recover without manual process cleanup.
        """

        normalized = (error_text or "").strip().lower()
        if not normalized:
            return False
        return any(token in normalized for token in SERIAL_OPEN_FORCE_RELEASE_ERROR_TOKENS)

    def _open_transport_with_retry(self, target_port: str) -> tuple[int, str]:
        """@brief Open transport with bounded retry for transient COM faults.

        @details Windows can temporarily reject COM re-open after USB resets
        with errors such as semaphore timeout. The runtime retries only known
        transient failures to avoid immediately latching an operator-visible
        blocking error state.

        @return Tuple of ``(attempts_used, last_error_text)`` where
                ``attempts_used == 0`` means all attempts failed.
        """

        last_error = ""
        for attempt in range(1, SERIAL_OPEN_RETRY_LIMIT + 1):
            try:
                serial_link_manager.open_port()
                return attempt, ""
            except RuntimeError as exc:
                last_error = str(exc).strip() or "generic unknown failure"
                transient = self._is_transient_serial_open_error(last_error)
                released_pids: list[int] | None = None
                release_error = ""
                if transient and attempt < SERIAL_OPEN_RETRY_LIMIT:
                    if self._should_force_release_on_open_error(last_error):
                        try:
                            _, released_pids = serial_link_manager.force_release_port()
                        except Exception as release_exc:
                            release_error = str(release_exc).strip() or "generic unknown failure"
                with self._lock:
                    if transient and attempt < SERIAL_OPEN_RETRY_LIMIT:
                        self._append_log(
                            f"Open {target_port} attempt {attempt}/{SERIAL_OPEN_RETRY_LIMIT} failed "
                            f"({last_error}); retrying."
                        )
                        if released_pids is not None:
                            detail = ", ".join(str(pid) for pid in released_pids) if released_pids else "none"
                            self._append_log(
                                f"Open {target_port} retry pre-step force-released COM holders: {detail}."
                            )
                        elif release_error:
                            self._append_log(
                                f"Open {target_port} retry pre-step COM force-release failed ({release_error})."
                            )
                    else:
                        self._append_log(
                            f"Open {target_port} attempt {attempt}/{SERIAL_OPEN_RETRY_LIMIT} failed ({last_error})."
                        )
                if not transient or attempt >= SERIAL_OPEN_RETRY_LIMIT:
                    break
                sleep(SERIAL_OPEN_RETRY_BASE_BACKOFF_S * float(attempt))
        return 0, last_error

    @staticmethod
    def _try_parse_u32_payload_value(payload_text: str, key_text: str) -> int | None:
        """@brief Parse one unsigned integer value from a semicolon payload.

        @details KEEPALIVE payloads carry `sid` and `req` correlation metadata.
        Exposing parsed values in logs keeps request/response tracing explicit.
        """

        key_prefix = f"{key_text}="
        for token in payload_text.split(";"):
            token = token.strip()
            if not token.startswith(key_prefix):
                continue
            raw_value = token[len(key_prefix):].strip()
            if raw_value.isdigit():
                return int(raw_value)
        return None

    @staticmethod
    def _try_parse_i32_payload_value(payload_text: str, key_text: str) -> int | None:
        """@brief Parse one signed integer value from a semicolon payload.

        @details Delay telemetry uses `-1` to indicate "not in keepalive
        sequence" while positive values remain measured latencies.
        """

        key_prefix = f"{key_text}="
        for token in payload_text.split(";"):
            token = token.strip()
            if not token.startswith(key_prefix):
                continue
            raw_value = token[len(key_prefix):].strip()
            if not raw_value:
                continue
            sign_trimmed = raw_value[1:] if raw_value.startswith("-") else raw_value
            if sign_trimmed.isdigit():
                try:
                    return int(raw_value)
                except ValueError:
                    return None
        return None

    @staticmethod
    def _try_parse_text_payload_value(payload_text: str, key_text: str) -> str | None:
        """@brief Parse one free-text value from a semicolon payload."""

        key_prefix = f"{key_text}="
        for token in payload_text.split(";"):
            token = token.strip()
            if not token.startswith(key_prefix):
                continue
            value_text = token[len(key_prefix):].strip()
            if value_text:
                return value_text
        return None

    def _handle_bridge_error_frame_locked(self, payload_text: str) -> bool:
        """@brief Handle known bridge-side error payloads non-fatally.

        @details Some bridge error payloads are informational state reports
        (for example client TCP closure) rather than protocol corruption.
        Handling them explicitly keeps the host runtime synchronized and avoids
        follow-up sends on stale sessions.

        @return ``True`` when the payload was handled.
        """

        if not payload_text:
            return False

        if payload_text.startswith("realtime_data_halted"):
            halt_reason = self._try_parse_text_payload_value(payload_text, "reason") or "unknown"
            self._append_log(f"Bridge realtime-data stream halted ({halt_reason}).")
            if halt_reason == "tcp_client_closed":
                self._watchdog_armed = False
                self._running_integer_seeded = False
                self._last_running_integer_rx_at = None
                self._tcp_connected = False
                self._connect_completed = False
                self._shot_done_pending = False
                self._shot_done_due_monotonic = 0.0
                self._set_state(
                    LinkState.CONNECT,
                    "Bridge reported TCP client closed; waiting for client reconnect.",
                )
            return True

        return False

    @staticmethod
    def _classify_keepalive_direction(payload_text: str) -> str:
        """@brief Classify one KEEPALIVE payload as request or response.

        @details The bridge emits `ka_req` while the client returns `ka_resp`.
        Legacy payloads are labeled explicitly so mixed firmware versions are
        still diagnosable from one logger view.
        """

        if payload_text.startswith("ka_req"):
            return "REQ"
        if payload_text.startswith("ka_resp"):
            return "RESP"
        if payload_text.startswith("keepalive"):
            return "LEGACY"
        return "UNKNOWN"

    def _describe_frame_for_log(self, frame: Frame, payload_text: str) -> str:
        """@brief Build optional per-frame log metadata for UI diagnostics.

        @details KEEPALIVE entries include direction plus `sid`/`req` so stale
        and duplicate traffic can be identified without cross-referencing raw
        payload text manually.
        """

        if frame.message_type is not MessageType.KEEPALIVE:
            return ""

        direction = self._classify_keepalive_direction(payload_text)
        sid = self._try_parse_u32_payload_value(payload_text, "sid")
        req = self._try_parse_u32_payload_value(payload_text, "req")

        details = f" direction={direction}"
        if sid is not None:
            details += f" sid={sid}"
        if req is not None:
            details += f" req={req}"
        return details

    @staticmethod
    def _try_parse_float_payload_value(payload_text: str, key_text: str) -> float | None:
        """@brief Parse one float value from a semicolon-delimited payload."""

        key_prefix = f"{key_text}="
        for token in payload_text.split(";"):
            token = token.strip()
            if not token.startswith(key_prefix):
                continue
            raw_value = token[len(key_prefix):].strip()
            if not raw_value:
                continue
            try:
                return float(raw_value)
            except ValueError:
                return None
        return None

    def _send_lcd_protocol_payload_locked(self, payload_text: str) -> None:
        """@brief Send one LCD protocol DATA payload while runtime lock is held.

        @details The protocol helper calls this hook in response to client
        bootstrap commands. Failures remain non-fatal and are recorded in logs.
        """

        try:
            self._send_command_locked(MessageType.DATA, payload_text)
        except RuntimeError as exc:
            self._append_log(f"LCD protocol payload could not be forwarded: {exc}")

    def _get_lcd_protocol_profiles(self) -> list[LCDControllerProfileSummary]:
        """@brief Build typed profile summaries for protocol catalog response."""

        sim_stats = data_payload_manager.get_stats()
        profile_presets = sim_stats.get("profiles", [])
        if not isinstance(profile_presets, list):
            return []
        return build_profile_summaries_from_presets(profile_presets)

    def _get_lcd_protocol_dataset(self) -> dict[str, object]:
        """@brief Return full settings+profiles dataset for protocol bootstrap."""

        return data_payload_manager.get_lcd_profile_dataset()

    def _handle_client_data_command_locked(self, payload_text: str) -> None:
        """@brief Apply client DATA text commands to simulator data generation.

        @details The client sends command-like DATA payloads such as
        `ProfileSelection`, `StartBrew`, and `DataSimulationOn`.
        """

        normalized = payload_text.strip().lower()
        if not normalized:
            return

        if self._lcd_protocol_bridge.handle_client_text_command(payload_text):
            self._append_log("Client LCD protocol bootstrap command handled.")
            return

        if normalized.startswith(DataSimulationEvent.PROFILE_SELECTION.value):
            profile_id = self._try_parse_u32_payload_value(normalized, "profile")
            if profile_id is None:
                profile_id = self._try_parse_u32_payload_value(normalized, "id")
            offline_value = self._try_parse_u32_payload_value(normalized, "offline")
            data_payload_manager.select_profile(
                profile_id=profile_id,
                offline=(offline_value is not None and offline_value != 0),
            )
            sim_stats = data_payload_manager.get_stats()
            self._append_log(
                "Client event ProfileSelection accepted "
                f"(profile={sim_stats['brew_profile_id']}:{sim_stats['brew_profile_name']})."
            )
            return

        if normalized.startswith(DataSimulationEvent.START_BREW.value):
            profile_id = self._try_parse_u32_payload_value(normalized, "profile")
            packet_interval_ms = self._try_parse_u32_payload_value(normalized, "packet_interval_ms")
            if packet_interval_ms is None:
                packet_interval_ms = self._try_parse_u32_payload_value(normalized, "int_ms")
            data_payload_manager.start_brew(profile_id=profile_id, packet_interval_ms=packet_interval_ms)
            sim_stats = data_payload_manager.get_stats()
            self._append_log(
                "Client event StartBrew accepted "
                f"(profile={sim_stats['brew_profile_id']}, "
                f"brew_time={sim_stats['brew_time_sec']:.1f}s, "
                f"interval={sim_stats['sim_packet_interval_ms']} ms)."
            )
            try:
                self._send_command_locked(MessageType.DATA, "StartBrew")
            except RuntimeError as exc:
                self._append_log(f"Client StartBrew acknowledgment could not be echoed: {exc}")
            return

        if normalized.startswith(DataSimulationEvent.STOP_BREW.value):
            data_payload_manager.stop_brew()
            self._append_log("Client event StopBrew accepted.")
            try:
                self._send_command_locked(MessageType.DATA, "StopBrew")
            except RuntimeError as exc:
                self._append_log(f"Client StopBrew acknowledgment could not be echoed: {exc}")
            return

        if normalized.startswith(DataSimulationEvent.ON.value):
            amplitude = self._try_parse_float_payload_value(normalized, "amp")
            if amplitude is None:
                amplitude = self._try_parse_float_payload_value(normalized, "amplitude")
            frequency_hz = self._try_parse_float_payload_value(normalized, "freq")
            if frequency_hz is None:
                frequency_hz = self._try_parse_float_payload_value(normalized, "frequency_hz")

            data_payload_manager.configure_sine(amplitude=amplitude, frequency_hz=frequency_hz)
            data_payload_manager.set_simulation_enabled(True)
            sim_stats = data_payload_manager.get_stats()
            self._append_log(
                "Client event DataSimulationOn accepted "
                f"(amp={sim_stats['sim_amplitude']:.3f}, freq={sim_stats['sim_frequency_hz']:.3f} Hz)."
            )
            return

        if normalized.startswith(DataSimulationEvent.OFF.value):
            data_payload_manager.set_simulation_enabled(False)
            self._append_log("Client event DataSimulationOFF accepted.")

    def _poll_received_frames_locked(self) -> None:
        """@brief Consume received frames and advance the server-side states.

        @details RX handling owns the asynchronous promotions from connect into
        keepalive, acknowledges keepalive progress, and enables send-data only
        after the ESP controller confirms the active transport session.
        """

        frames = serial_link_manager.pop_received_frames()
        if not frames:
            return

        for frame in frames:
            payload_text = frame.payload.decode("utf-8", errors="ignore").strip().lower()
            self._ingest_firmware_telemetry_from_payload_locked(payload_text)
            frame_log_details = self._describe_frame_for_log(frame, payload_text)
            self._append_log(
                f"Received {frame.message_type.name} seq={frame.sequence} "
                f"server={frame.host_live_integer} client={frame.device_live_integer}{frame_log_details}."
            )

            if (
                self._current_state in (LinkState.KEEPALIVE_SERVER_SEND, LinkState.KEEPALIVE_CLIENT_RETURN)
                and self._last_peer_sequence is not None
                and frame.sequence != ((self._last_peer_sequence + 1) % 65536)
            ):
                self._bottom_layer_sequence_error_count += 1
                self._append_log("TopLayer sequential communication loss detected; monitoring continues.")
            self._last_peer_sequence = frame.sequence

            if frame.message_type == MessageType.ERROR:
                self._record_total_error_locked(payload_text or "bridge_error")
                if payload_text == "keepalive_retry_exhausted":
                    self._watchdog_armed = False
                    self._running_integer_seeded = False
                    self._last_running_integer_rx_at = None
                    self._wifi_connected = False
                    self._tcp_connected = False
                    self._set_state(
                        LinkState.INITIALIZE,
                        "Bridge keepalive retries exhausted. Bridge returned to initialize wait.",
                    )
                    continue
                if self._handle_bridge_error_frame_locked(payload_text):
                    continue
                self._append_log(f"Bridge reported error frame: {payload_text or 'generic unknown failure'}")
                continue

            if frame.message_type in (MessageType.KEEPALIVE, MessageType.DATA):
                now_utc = datetime.now(UTC)
                if frame.message_type is MessageType.KEEPALIVE:
                    keepalive_direction = self._classify_keepalive_direction(payload_text)
                    if keepalive_direction == "REQ":
                        if ((frame.host_live_integer & 1) != 0 or
                            (frame.device_live_integer & 1) == 0 or
                            (frame.device_live_integer + 1) != frame.host_live_integer):
                            self._append_log(
                                (
                                    "USB_TransportKeepAlive_ServerSend ignored due to parity/counter mismatch; "
                                    "waiting for timeout retry."
                                )
                            )
                            continue

                        self._keepalive_req_count += 1
                        if self._session_started_at is None:
                            self._session_started_at = now_utc
                        self._running_integer_seeded = True
                        self._server_live_integer = frame.host_live_integer
                        self._client_live_integer = frame.host_live_integer + 1
                        self._last_running_integer_rx_at = now_utc
                        self._watchdog_armed = True
                        self._wifi_connected = True
                        self._tcp_connected = True
                        self._bridge_ready = True
                        self._initialize_completed = True
                        self._connect_completed = True
                        self._clear_connection_fault_locked()
                        self._bottom_layer_retry_count = 0
                        self._pending_auto_stage = None
                        self._set_state(
                            LinkState.KEEPALIVE_CLIENT_RETURN,
                            (
                                "USB_TransportKeepAlive_ServerSend observed; waiting for "
                                "USB_TransportKeepAlive_ClientReturn."
                            ),
                        )
                        continue

                    if keepalive_direction == "RESP":
                        if ((frame.host_live_integer & 1) != 0 or
                            (frame.device_live_integer & 1) == 0 or
                            frame.host_live_integer != (frame.device_live_integer + 1)):
                            self._append_log(
                                (
                                    "USB_TransportKeepAlive_ClientReturn ignored due to parity/counter mismatch; "
                                    "bridge timeout handling remains active."
                                )
                            )
                            continue

                        self._keepalive_resp_count += 1
                        self._running_integer_seeded = True
                        self._server_live_integer = frame.host_live_integer
                        self._client_live_integer = frame.device_live_integer
                        self._last_running_integer_rx_at = now_utc
                        self._watchdog_armed = True
                        self._wifi_connected = True
                        self._tcp_connected = True
                        self._bridge_ready = True
                        self._initialize_completed = True
                        self._connect_completed = True
                        self._clear_connection_fault_locked()
                        self._bottom_layer_retry_count = 0
                        self._set_state(
                            LinkState.KEEPALIVE_SERVER_SEND,
                            (
                                "USB_TransportKeepAlive_ClientReturn validated; waiting for next "
                                "USB_TransportKeepAlive_ServerSend."
                            ),
                        )
                        continue

                if frame.message_type is MessageType.DATA:
                    self._data_frames_rx_count += 1
                    if frame.payload and frame.payload[0] == DATA_MAGIC_UPLINK:
                        # Binary uplink data packet — route to FIFO for app consumption.
                        data_payload_manager.receive_uplink(bytes(frame.payload))
                    else:
                        received_text = frame.payload.decode("utf-8", errors="replace").strip()
                        self._last_received_client_text = received_text or "Empty client payload"
                        self._handle_client_data_command_locked(received_text)
                    continue

            if payload_text == "connect_ack":
                self._wifi_connected = True
                self._tcp_connected = False
                self._bridge_ready = True
                self._set_state(
                    LinkState.CONNECT,
                    "Bridge acknowledged CONNECT and remains in connect wait.",
                )
                continue

            if payload_text == "tcp_connected":
                self._wifi_connected = True
                self._tcp_connected = True
                self._bridge_ready = True
                self._set_state(
                    LinkState.CONNECT,
                    "Bridge TCP transport connected. Waiting for client CONNECT frame.",
                )
                continue

            if payload_text.startswith("client_connected"):
                self._pending_auto_stage = None
                self._connect_completed = True
                self._watchdog_armed = False
                self._wifi_connected = True
                self._tcp_connected = True
                self._bridge_ready = True
                self._set_state(
                    LinkState.CONNECT,
                    "Bridge acknowledged client CONNECT handshake.",
                )
                continue

            if payload_text == "disconnect_ack":
                self._tcp_connected = False
                self._watchdog_armed = False
                self._set_state(
                    LinkState.DISCONNECT,
                    "Bridge acknowledged disconnect request.",
                )
                continue

            if payload_text == "reset_ack":
                self._clear_runtime_flow_locked()
                self._wifi_ready = False
                self._wifi_connected = False
                self._tcp_connected = False
                self._bridge_ready = True
                self._set_state(
                    LinkState.RESET,
                    "Bridge acknowledged reset request.",
                )
                continue

            if payload_text == "initialize_ack":
                self._wifi_ready = True
                self._wifi_connected = False
                self._tcp_connected = False
                self._bridge_ready = True
                self._initialize_completed = True
                self._set_state(
                    LinkState.INITIALIZE,
                    "Bridge acknowledged initialize request.",
                )
                continue

            if self._current_state is LinkState.CONNECT and payload_text in CONNECT_SUCCESS_PAYLOADS:
                self._pending_auto_stage = None
                self._connect_completed = True
                self._watchdog_armed = False
                self._wifi_connected = True
                self._tcp_connected = True
                self._bridge_ready = True
                self._append_log(
                    "Bridge connect success observed. Waiting for keepalive confirmation."
                )
                continue

    def _check_port_alive_locked(self) -> None:
        """@brief Detect unexpected serial port closure and route to error state.

        @details The serial reader thread detaches the port handle after a read
        failure (e.g. USB glitch, bridge watchdog reset) without informing the
        state machine directly.  This helper is polled on every snapshot so the
        machine transitions to ERROR as soon as the closure is observed rather
        than staying stuck in an active state indefinitely with silent data drops.
        Skip the check in RESET, ERROR, and DISCONNECT — those states either do
        not expect the port to be open or have already handled the fault.
        """

        if self._current_state in (LinkState.RESET, LinkState.ERROR, LinkState.DISCONNECT):
            return
        if not serial_link_manager.get_snapshot().port_open:
            self._set_runtime_fault_locked("Serial port disconnected unexpectedly.")

    def _enforce_running_integer_timeout_locked(self) -> None:
        """@brief Note prolonged wait time in KeepAliveClientReturn.

        @details The bridge firmware now owns timeout retries. The simulator
        monitor records long waits but does not schedule additional retries on
        its own, avoiding duplicate retry paths.
        """

        if not self._running_integer_seeded:
            return
        if self._current_state is not LinkState.KEEPALIVE_CLIENT_RETURN:
            return
        if self._last_running_integer_rx_at is None:
            return
        if datetime.now(UTC) - self._last_running_integer_rx_at <= timedelta(milliseconds=RUNNING_INTEGER_TIMEOUT_MS):
            return

        self._watchdog_timeout_count += 1
        self._append_log(
            (
                "Monitor note: USB_TransportKeepAlive_ClientReturn exceeded timeout window; "
                "waiting for bridge retry handling."
            )
        )
        self._last_running_integer_rx_at = datetime.now(UTC)

    def _send_command_locked(self, message_type: MessageType, payload_text: str = "") -> None:
        frame = Frame(
            message_type=message_type,
            host_live_integer=self._server_live_integer,
            device_live_integer=self._client_live_integer,
            sequence=self._next_sequence(),
            payload=payload_text.encode("utf-8"),
        )
        serial_link_manager.send_frame(frame)
        if message_type is MessageType.DATA:
            self._data_frames_tx_count += 1
        self._append_log(f"Sent {message_type.name} seq={frame.sequence} server={frame.host_live_integer}.")

    def _send_binary_locked(self, message_type: MessageType, payload: bytes) -> None:
        """@brief Send one frame with a raw binary payload.

        @details Used by the data payload channel which carries packed structs
        rather than UTF-8 text.  Increments the DATA TX counter when the
        message type is DATA.

        @param message_type Frame type.
        @param payload      Raw bytes for the payload field.
        """
        frame = Frame(
            message_type=message_type,
            host_live_integer=self._server_live_integer,
            device_live_integer=self._client_live_integer,
            sequence=self._next_sequence(),
            payload=payload,
        )
        serial_link_manager.send_frame(frame)
        if message_type is MessageType.DATA:
            self._data_frames_tx_count += 1
        self._append_log(
            f"Sent {message_type.name} (binary {len(payload)}B) seq={frame.sequence} server={frame.host_live_integer}."
        )

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

        open_attempts, open_error = self._open_transport_with_retry(target_port)
        if open_attempts == 0:
            with self._lock:
                self._bridge_ready = False
                self._set_runtime_fault_locked(
                    f"COM open failed on {target_port}: {open_error or 'COM port not found'}"
                )
                return self._snapshot_locked()

        with self._lock:
            self._bridge_ready = True
            if open_attempts > 1:
                self._append_log(
                    f"Serial transport opened on {target_port} after {open_attempts} attempts."
                )
            self._append_log(
                "Serial transport opened. Forcing clean bridge reset before accepting transport traffic."
            )

        serial_link_manager.clear_buffers()
        data_payload_manager.start(self.send_downlink_data_packet)
        return self.reset()

    def close_transport(self) -> LinkSnapshot:
        data_payload_manager.stop()
        with self._lock:
            serial_link_manager.close_port()
            self._clear_runtime_flow_locked()
            self._bridge_ready = False
            self._wifi_ready = False
            self._wifi_connected = False
            self._tcp_connected = False
            self._set_state(LinkState.RESET, "Serial transport closed.")
            return self._snapshot_locked()

    def force_release_transport(self) -> LinkSnapshot:
        """@brief Force-release the configured COM port from all likely holders.

        @details Stops the data payload background thread first (must happen
        outside the lock to avoid a deadlock with the send callback), closes
        the simulator-owned handle, then hard-stops external processes that
        match the configured COM port and known serial tooling patterns.
        """

        data_payload_manager.stop()
        with self._lock:
            snapshot, released_pids = serial_link_manager.force_release_port()
            self._clear_runtime_flow_locked()
            self._bridge_ready = False
            self._wifi_ready = False
            self._wifi_connected = False
            self._tcp_connected = False
            self._clear_connection_fault_locked()
            self._bottom_layer_retry_count = 0
            self._top_layer_failure_count = 0
            self._top_layer_connect_streak = 0
            self._last_peer_sequence = None
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
            self._bottom_layer_retry_count = 0
            self._top_layer_failure_count = 0
            self._top_layer_connect_streak = 0
            self._last_peer_sequence = None
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
            self._bottom_layer_retry_count = 0
            self._top_layer_failure_count = 0
            self._top_layer_connect_streak = 0
            self._last_peer_sequence = None
            self._session_started_at = None
            self._keepalive_req_count = 0
            self._keepalive_resp_count = 0
            self._data_frames_rx_count = 0
            self._data_frames_tx_count = 0
            self._transport_min_delay_ms = 0
            self._ka_timing_valid = False
            self._watchdog_timeout_count = 0
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
            if self._current_state is LinkState.ERROR:
                self._append_log("Send data ignored because Reset Communication is required to clear the latched fault.")
                return self._snapshot_locked()
            if self._current_state not in (LinkState.KEEPALIVE_SERVER_SEND, LinkState.KEEPALIVE_CLIENT_RETURN):
                self._append_log(
                    "Send data ignored because keepalive-ready connection is not available yet."
                )
                return self._snapshot_locked()
            if not self._initialize_completed or not self._connect_completed:
                # Keepalive traffic is authoritative proof that init/connect
                # completed even if transitional bookkeeping lagged.
                self._initialize_completed = True
                self._connect_completed = True
            if not self._try_send_command_locked(
                MessageType.DATA,
                payload_text,
                "send data transmit failed",
            ):
                return self._snapshot_locked()
            self._set_state(self._current_state, "Send-data command issued during keepalive.")
            return self._snapshot_locked()

    def configure_data_simulation(
        self,
        *,
        enabled: bool | None = None,
        amplitude: float | None = None,
        frequency_hz: float | None = None,
        packet_interval_ms: int | None = None,
    ) -> LinkSnapshot:
        """@brief Update simulator sine-wave generator controls from the UI.

        @details This command surface is used by the dedicated simulator
        `Screen 6` control panel. The binary downlink sender still emits only
        while keepalive is active; enabling simulation ahead of keepalive is
        allowed and will start transmitting automatically once the session is
        ready.

        @param enabled      Optional stream enable flag.
        @param amplitude    Optional sine amplitude value.
        @param frequency_hz Optional sine frequency in Hz.
        @param packet_interval_ms Optional packet interval in milliseconds.
        """

        # Keep this control path side-effect free for transport state:
        # UI parameter edits must not consume RX frames or advance the
        # lifecycle state machine.
        data_payload_manager.configure_sine(
            amplitude=amplitude,
            frequency_hz=frequency_hz,
            packet_interval_ms=packet_interval_ms,
        )
        if enabled is not None:
            data_payload_manager.set_simulation_enabled(enabled)
        sim_stats = data_payload_manager.get_stats()

        with self._lock:
            if enabled is not None:
                # Mirror Screen 6 ON/OFF toggles to the TCP client so the
                # client-side Simulate Data toggle stays synchronized.
                transport_snapshot = serial_link_manager.get_snapshot()
                simulation_event_text = "DataSimulationOn" if sim_stats["sim_enabled"] else "DataSimulationOFF"
                if transport_snapshot.port_open:
                    try:
                        self._send_command_locked(MessageType.DATA, simulation_event_text)
                        self._append_log(f"Screen 6 forwarded {simulation_event_text} to client.")
                    except RuntimeError as exc:
                        self._append_log(
                            f"Screen 6 could not forward {simulation_event_text} to client: {exc}"
                        )

            if enabled is None:
                self._append_log(
                    "Screen 6 updated simulation parameters "
                    f"(amp={sim_stats['sim_amplitude']:.3f}, "
                    f"freq={sim_stats['sim_frequency_hz']:.3f} Hz, "
                    f"interval={sim_stats['sim_packet_interval_ms']} ms)."
                )
            else:
                self._append_log(
                    "Screen 6 set simulation "
                    f"{'ON' if sim_stats['sim_enabled'] else 'OFF'} "
                    f"(amp={sim_stats['sim_amplitude']:.3f}, "
                    f"freq={sim_stats['sim_frequency_hz']:.3f} Hz, "
                    f"interval={sim_stats['sim_packet_interval_ms']} ms)."
                )
            return self._snapshot_locked()

    def configure_lcd_home_data(
        self,
        *,
        profile_id: int | None = None,
        temperature_c: float | None = None,
        water_level_pct: float | None = None,
        weight_g: float | None = None,
        shot_target_g: float | None = None,
        warmup_on: bool | None = None,
        steam_on: bool | None = None,
    ) -> LinkSnapshot:
        """@brief Update Screen 7 Home-page values forwarded to the client LCD."""

        started_home_stream = False
        data_payload_manager.configure_sine(packet_interval_ms=1000)
        data_payload_manager.configure_lcd_home_data(
            profile_id=profile_id,
            temperature_c=temperature_c,
            water_level_pct=water_level_pct,
            weight_g=weight_g,
            shot_target_g=shot_target_g,
            warmup_on=warmup_on,
            steam_on=steam_on,
        )
        sim_stats = data_payload_manager.get_stats()
        if not bool(sim_stats.get("brew_active", False)) and not bool(sim_stats.get("sim_enabled", False)):
            data_payload_manager.set_simulation_enabled(True)
            started_home_stream = True
            sim_stats = data_payload_manager.get_stats()

        with self._lock:
            transport_snapshot = serial_link_manager.get_snapshot()
            if transport_snapshot.port_open and bool(sim_stats.get("sim_enabled", False)):
                try:
                    self._send_command_locked(MessageType.DATA, "DataSimulationOn")
                    if started_home_stream:
                        self._append_log("Screen 7 enabled 1 Hz simulation stream for Home data forwarding.")
                except RuntimeError as exc:
                    self._append_log(f"Screen 7 could not forward DataSimulationOn to client: {exc}")

            if profile_id is not None:
                if transport_snapshot.port_open:
                    profile_cmd = f"ProfileSelection;profile={int(sim_stats['brew_profile_id'])};offline=0"
                    try:
                        self._send_command_locked(MessageType.DATA, profile_cmd)
                    except RuntimeError as exc:
                        self._append_log(f"Screen 7 could not forward profile selection to client: {exc}")

            self._append_log(
                "Screen 7 updated Home values "
                f"(profile={sim_stats['brew_profile_id']}, "
                f"temp={float(sim_stats.get('home_temperature_c', 0.0)):.1f} C, "
                f"water={float(sim_stats.get('home_water_level_pct', 0.0)):.1f}%, "
                f"weight={float(sim_stats.get('home_weight_g', 0.0)):.1f} g, "
                f"target={float(sim_stats.get('home_shot_target_g', 0.0)):.1f} g, "
                f"warmup={'ON' if bool(sim_stats.get('home_warmup_on', False)) else 'OFF'}, "
                f"steam={'ON' if bool(sim_stats.get('home_steam_on', False)) else 'OFF'}."
            )
            return self._snapshot_locked()

    def send_downlink_data_packet(self, payload: bytes) -> None:
        """@brief Send one binary downlink data packet if the session is active.

        @details Called from the data_payload background thread at the
        configured packet interval. The call is a no-op when the runtime is
        outside the keepalive states so no error is raised and the packet is
        silently dropped.

        @param payload Packed bytes from ``data_payload.encode_downlink()``.
        """
        try:
            frame: Frame
            with self._lock:
                if self._current_state not in (
                    LinkState.KEEPALIVE_SERVER_SEND,
                    LinkState.KEEPALIVE_CLIENT_RETURN,
                ):
                    return
                # Keep runtime lock hold time minimal: prepare the frame while
                # protected, then perform the potentially blocking serial write
                # outside the runtime lock.
                frame = Frame(
                    message_type=MessageType.DATA,
                    host_live_integer=self._server_live_integer,
                    device_live_integer=self._client_live_integer,
                    sequence=self._next_sequence(),
                    payload=payload,
                )
            try:
                serial_link_manager.send_frame(frame)
            except RuntimeError:
                return  # serial port not available; drop silently
            with self._lock:
                self._data_frames_tx_count += 1
        except Exception as exc:
            print(f"\n[link-runtime] UNHANDLED CRASH in send_downlink_data_packet: {exc}", flush=True)
            traceback.print_exc()

    def reset_total_errors(self) -> LinkSnapshot:
        """@brief Reset the aggregate telemetry error counter to zero."""

        with self._lock:
            transport_snapshot = serial_link_manager.get_snapshot()
            if transport_snapshot.port_open:
                try:
                    self._send_command_locked(MessageType.DATA, "telemetry_reset")
                except RuntimeError as exc:
                    self._append_log(f"Telemetry reset command failed: {exc}")
            self._total_error_count = 0
            self._append_log("Telemetry reset: total error counter cleared.")
            return self._snapshot_locked()

    def reset_transport_max_delay(self) -> LinkSnapshot:
        """@brief Reset only the transport maximum-delay telemetry value.

        @details The simulator routes this reset to the ESP32-C3 controller so
        the authoritative firmware-owned max-delay counter is cleared at the
        source, then mirrors the zeroed value in the current snapshot.
        """

        with self._lock:
            transport_snapshot = serial_link_manager.get_snapshot()
            if transport_snapshot.port_open:
                try:
                    self._send_command_locked(MessageType.DATA, "telemetry_reset_max_delay")
                except RuntimeError as exc:
                    self._append_log(f"Transport max-delay reset command failed: {exc}")
            self._transport_max_delay_ms = 0
            self._append_log("Telemetry reset: transport max delay cleared.")
            return self._snapshot_locked()

    def get_snapshot(self, *, include_logs: bool = True) -> LinkSnapshot:
        with self._lock:
            self._poll_received_frames_locked()
            self._check_port_alive_locked()
            self._enforce_running_integer_timeout_locked()
            self._advance_automatic_flow_locked()
            return self._snapshot_locked(include_logs=include_logs)

    def _snapshot_locked(self, *, include_logs: bool = True) -> LinkSnapshot:
        transport_snapshot: SerialLinkSnapshot = serial_link_manager.get_snapshot()
        self._update_usb_throughput_locked(transport_snapshot)
        dp_stats = data_payload_manager.get_stats()
        brew_active = bool(dp_stats.get("brew_active", False))
        now_monotonic = monotonic()
        if self._last_brew_active and not brew_active:
            self._shot_done_pending = True
            self._shot_done_due_monotonic = now_monotonic + SHOT_DONE_SUCCESS_DELAY_SEC
            self._append_log(
                f"Brew duration reached. Scheduling shot_done_success in {SHOT_DONE_SUCCESS_DELAY_SEC:.1f}s."
            )
        if brew_active:
            self._shot_done_pending = False
            self._shot_done_due_monotonic = 0.0
        if (
            self._shot_done_pending
            and not brew_active
            and now_monotonic >= self._shot_done_due_monotonic
            and transport_snapshot.port_open
            and self._tcp_connected
            and self._current_state in (
                LinkState.KEEPALIVE_SERVER_SEND,
                LinkState.KEEPALIVE_CLIENT_RETURN,
            )
        ):
            try:
                self._send_command_locked(MessageType.DATA, "shot_done_success")
                self._append_log(
                    f"Delayed shot_done_success sent to client after {SHOT_DONE_SUCCESS_DELAY_SEC:.1f}s."
                )
                self._shot_done_pending = False
                self._shot_done_due_monotonic = 0.0
            except RuntimeError as exc:
                self._append_log(f"shot_done_success event could not be forwarded to client: {exc}")
        elif (
            self._shot_done_pending
            and not brew_active
            and now_monotonic >= self._shot_done_due_monotonic
        ):
            self._shot_done_pending = False
            self._shot_done_due_monotonic = 0.0
            self._append_log(
                "Skipped shot_done_success because keepalive session is no longer active."
            )
        self._last_brew_active = brew_active
        esp32c3_connected = bool(transport_snapshot.port_open and self._bridge_ready)
        if not transport_snapshot.port_open:
            esp32c3_link_state = "disconnected"
        elif self._bridge_ready:
            esp32c3_link_state = "connected"
        else:
            esp32c3_link_state = "syncing"
        important_data = {
            "Current State": self._state_display_name(self._current_state),
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
            "Transport Last Delay [mS]": str(self._transport_last_delay_ms),
            "Transport Max Delay [mS]": str(self._transport_max_delay_ms),
            "BottomLayer Retries": str(self._bottom_layer_retry_count),
            "TopLayer Failures": str(self._top_layer_failure_count),
            "Total Errors": str(self._total_error_count),
            "TopLayer Connect Streak": str(self._top_layer_connect_streak),
            "BottomLayer Checksum Errors": str(self._bottom_layer_checksum_error_count),
            "BottomLayer Sequence Errors": str(self._bottom_layer_sequence_error_count),
            "Text Received From Client": self._last_received_client_text,
            "Data Simulation": "On" if dp_stats["sim_enabled"] else "Off",
            "StartBrew Event": "On" if brew_active else "Off",
            "LCD Active Profile ID": str(int(dp_stats.get("brew_profile_id", 1))),
            "Brew Profile": str(dp_stats.get("brew_profile_name", "")),
            "Brew Time [Sec]": f"{float(dp_stats.get('brew_time_sec', 0.0)):.1f}",
            "Brew Elapsed [Sec]": f"{float(dp_stats.get('brew_elapsed_sec', 0.0)):.1f}",
            "Brew Remaining [Sec]": f"{float(dp_stats.get('brew_remaining_sec', 0.0)):.1f}",
            "LCD Home Pressure [Bar]": f"{float(dp_stats.get('home_pressure_bar', 0.0)):.2f}",
            "LCD Home Temperature [C]": f"{float(dp_stats.get('home_temperature_c', 0.0)):.1f}",
            "LCD Home Water Level [%]": f"{float(dp_stats.get('home_water_level_pct', 0.0)):.1f}",
            "LCD Home Weight [g]": f"{float(dp_stats.get('home_weight_g', 0.0)):.1f}",
            "LCD Home Shot Target [g]": f"{float(dp_stats.get('home_shot_target_g', 0.0)):.1f}",
            "LCD Home Warmup": "On" if bool(dp_stats.get("home_warmup_on", False)) else "Off",
            "LCD Home Steam": "On" if bool(dp_stats.get("home_steam_on", False)) else "Off",
            "LCD Profile Count": str(dp_stats.get("profile_count", 0)),
            "LCD Profiles": " | ".join(
                str(profile.get("name", "Profile"))
                for profile in dp_stats.get("profiles", [])
            ) or "None",
            "Simulation Amplitude": f"{dp_stats['sim_amplitude']:.3f}",
            "Simulation Frequency [Hz]": f"{dp_stats['sim_frequency_hz']:.3f}",
            "Packet Interval [mSec]": str(dp_stats["sim_packet_interval_ms"]),
            "Data Throughput [kByte/Sec]": f"{dp_stats['sim_data_throughput_kbytes_per_sec']:.3f}",
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
        # --- Compute enhanced telemetry_data ---
        if self._session_started_at is not None:
            elapsed = datetime.now(UTC) - self._session_started_at
            total_s = int(elapsed.total_seconds())
            h, rem = divmod(total_s, 3600)
            m, s = divmod(rem, 60)
            session_uptime = f"{h:02d}:{m:02d}:{s:02d}"
        else:
            session_uptime = "No session"
        jitter_ms = (self._transport_max_delay_ms - self._transport_min_delay_ms) if self._ka_timing_valid else 0
        total_ka = self._keepalive_req_count + self._bottom_layer_sequence_error_count
        if total_ka > 0:
            loss_x10 = (self._bottom_layer_sequence_error_count * 1000) // total_ka
            pkt_loss = f"{loss_x10 // 10}.{loss_x10 % 10}%"
        else:
            pkt_loss = "0.0%"
        telemetry_data = {
            "Session Uptime": session_uptime,
            "Keepalive REQ Received": str(self._keepalive_req_count),
            "Keepalive RESP Received": str(self._keepalive_resp_count),
            "Data Frames RX": str(self._data_frames_rx_count),
            "Data Frames TX": str(self._data_frames_tx_count),
            "Transport Min Delay [mS]": str(self._transport_min_delay_ms),
            "Transport Jitter [mS]": str(jitter_ms),
            "Watchdog Timeouts": str(self._watchdog_timeout_count),
            "Packet Loss (est.)": pkt_loss,
            "Serial TX Frames": str(transport_snapshot.tx_frames),
            "Serial RX Frames": str(transport_snapshot.rx_frames),
            "Serial TX Bytes": str(transport_snapshot.tx_bytes),
            "Serial RX Bytes": str(transport_snapshot.rx_bytes),
            "USB RX Throughput [kB/s]": f"{self._usb_rx_kbytes_per_sec:.2f}",
            "USB TX Throughput [kB/s]": f"{self._usb_tx_kbytes_per_sec:.2f}",
            "USB Total Throughput [kB/s]": f"{self._usb_total_kbytes_per_sec:.2f}",
            "DL Packets Sent": str(dp_stats["dl_tx_count"]),
            "DL Sequence": str(dp_stats["dl_seq"]),
            "Simulation Enabled": "Yes" if dp_stats["sim_enabled"] else "No",
            "StartBrew Event": "Yes" if brew_active else "No",
            "Brew Profile": str(dp_stats.get("brew_profile_name", "")),
            "Brew Time [Sec]": f"{float(dp_stats.get('brew_time_sec', 0.0)):.1f}",
            "Brew Elapsed [Sec]": f"{float(dp_stats.get('brew_elapsed_sec', 0.0)):.1f}",
            "Brew Remaining [Sec]": f"{float(dp_stats.get('brew_remaining_sec', 0.0)):.1f}",
            "Samples/Channel": str(dp_stats.get("brew_samples_per_channel", 0)),
            "Simulation Amplitude": f"{dp_stats['sim_amplitude']:.3f}",
            "Simulation Frequency [Hz]": f"{dp_stats['sim_frequency_hz']:.3f}",
            "Packet Interval [mSec]": str(dp_stats["sim_packet_interval_ms"]),
            "Data Throughput [kByte/Sec]": f"{dp_stats['sim_data_throughput_kbytes_per_sec']:.3f}",
            "UL Packets Received": str(dp_stats["ul_rx_count"]),
            "UL Packets Dropped": str(dp_stats["ul_drop_count"]),
            "UL FIFO Depth": str(dp_stats["ul_fifo_depth"]),
            "Last UL Seq": str(dp_stats["last_ul_seq"]),
            "Last UL Preview": str(dp_stats.get("home_last_uplink_preview", "")),
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
            bottom_layer_retry_count=self._bottom_layer_retry_count,
            top_layer_failure_count=self._top_layer_failure_count,
            top_layer_connect_streak=self._top_layer_connect_streak,
            bottom_layer_checksum_error_count=self._bottom_layer_checksum_error_count,
            bottom_layer_sequence_error_count=self._bottom_layer_sequence_error_count,
            sequence=self._sequence,
            transport_last_delay_ms=self._transport_last_delay_ms,
            transport_max_delay_ms=self._transport_max_delay_ms,
            total_error_count=self._total_error_count,
            esp32c3_connected=esp32c3_connected,
            esp32c3_link_state=esp32c3_link_state,
            usb_rx_kbytes_per_sec=self._usb_rx_kbytes_per_sec,
            usb_tx_kbytes_per_sec=self._usb_tx_kbytes_per_sec,
            usb_total_kbytes_per_sec=self._usb_total_kbytes_per_sec,
            last_error=self._last_error or transport_snapshot.last_error,
            important_data=important_data,
            telemetry_data=telemetry_data,
            profile_dataset=data_payload_manager.get_lcd_profile_dataset(),
            transport=asdict(transport_snapshot),
            logs=self._combined_logs_locked() if include_logs else [],
            low_level_logs=serial_link_manager.get_logs()[:2000] if include_logs else [],
            available_states=[state.value for state in LinkState],
        )


link_runtime = LinkRuntime()
