"""Controller simulation state machine and runtime snapshot helpers."""

from __future__ import annotations

from collections import deque
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from enum import StrEnum
from threading import Lock

from server.transport.serial_link import SerialLinkSnapshot, serial_link_manager


class SimulatorState(StrEnum):
    LOAD_SIM = "LoadSim"
    CONNECT_TO_CLIENT = "Connect2Client"
    INITIALIZE_MACHINE = "InitializeMachine"
    BREW = "Brew"
    IDLE = "Idle"


@dataclass(slots=True)
class SimulatorSnapshot:
    current_state: str
    client_ip_address: str
    last_transition_at: str
    machine_initialized: bool
    connected_to_client: bool
    brew_active: bool
    shot_timer_seconds: int
    brew_temperature_c: str
    pressure_bar: str
    transport: dict[str, object]
    important_data: dict[str, str]
    logs: list[str]
    available_states: list[str]


class SimulatorRuntime:
    """@brief Own the mutable simulator state and log buffer."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._logs: deque[str] = deque(maxlen=300)
        self._client_ip_address = ""
        self._current_state = SimulatorState.LOAD_SIM
        self._last_transition_at = self._timestamp()
        self._machine_initialized = False
        self._connected_to_client = False
        self._brew_active = False
        self._brew_started_at: datetime | None = None
        self._last_command = "Simulator loaded"
        self._append_log("Simulator runtime loaded and waiting for client connection.")

    def _timestamp(self) -> str:
        return datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%SZ")

    def _append_log(self, message: str) -> None:
        self._logs.append(f"[{self._timestamp()}] {message}")

    def _set_state(self, state: SimulatorState, message: str) -> None:
        self._current_state = state
        self._last_transition_at = self._timestamp()
        self._append_log(message)

    def connect_to_client(self, client_ip_address: str) -> SimulatorSnapshot:
        normalized_ip = client_ip_address.strip()
        if not normalized_ip:
            raise ValueError("Client IP address is required.")

        with self._lock:
            self._client_ip_address = normalized_ip
            serial_link_manager.configure_target(normalized_ip)
            self._set_state(
                SimulatorState.CONNECT_TO_CLIENT,
                f"Connecting simulator backend to client at {normalized_ip}.",
            )
            serial_link_manager.connect()
            self._connected_to_client = True
            self._last_command = "Connect"
            self._set_state(
                SimulatorState.INITIALIZE_MACHINE,
                "Client link established. Initializing machine simulation services.",
            )
            self._machine_initialized = True
            self._append_log("Boiler model, telemetry loop, and brew workflow stubs initialized.")
            self._set_state(
                SimulatorState.IDLE,
                "Machine initialization complete. Simulator entered Idle state.",
            )
            return self._snapshot_locked()

    def initialize_machine_only(self) -> SimulatorSnapshot:
        with self._lock:
            if not self._client_ip_address:
                raise ValueError("Client IP address must be set before initialization.")
            self._connected_to_client = True
            self._machine_initialized = False
            self._last_command = "Initialize Machine"
            self._set_state(
                SimulatorState.INITIALIZE_MACHINE,
                "Machine initialization state entered manually for inspection.",
            )
            return self._snapshot_locked()

    def start_brew(self) -> SimulatorSnapshot:
        with self._lock:
            if not self._machine_initialized:
                raise ValueError("Machine must be initialized before starting Brew.")

            self._brew_active = True
            self._brew_started_at = datetime.now(UTC)
            self._last_command = "Start Brew"
            self._set_state(SimulatorState.BREW, "Brew session started.")
            return self._snapshot_locked()

    def go_idle(self) -> SimulatorSnapshot:
        with self._lock:
            self._brew_active = False
            self._brew_started_at = None
            self._machine_initialized = True
            self._last_command = "Return Idle"
            self._set_state(SimulatorState.IDLE, "Simulator returned to Idle state.")
            return self._snapshot_locked()

    def reset_to_load(self) -> SimulatorSnapshot:
        with self._lock:
            self._brew_active = False
            self._brew_started_at = None
            self._machine_initialized = False
            self._connected_to_client = False
            self._client_ip_address = ""
            self._last_command = "Reset LoadSim"
            serial_link_manager.disconnect()
            self._set_state(SimulatorState.LOAD_SIM, "Simulator reset to LoadSim.")
            return self._snapshot_locked()

    def set_state(self, state: SimulatorState) -> SimulatorSnapshot:
        if state is SimulatorState.LOAD_SIM:
            return self.reset_to_load()
        if state is SimulatorState.CONNECT_TO_CLIENT:
            return self.connect_to_client(self._client_ip_address)
        if state is SimulatorState.INITIALIZE_MACHINE:
            return self.initialize_machine_only()
        if state is SimulatorState.BREW:
            return self.start_brew()
        return self.go_idle()

    def get_snapshot(self) -> SimulatorSnapshot:
        with self._lock:
            return self._snapshot_locked()

    def _build_telemetry_locked(self) -> tuple[int, str, str]:
        shot_seconds = 0
        if self._brew_active and self._brew_started_at is not None:
            shot_seconds = int((datetime.now(UTC) - self._brew_started_at).total_seconds())

        if self._brew_active:
            temperature = 93.0 + min(shot_seconds, 8) * 0.15
            pressure = min(9.2, 1.8 + shot_seconds * 0.85)
        elif self._machine_initialized:
            temperature = 92.4
            pressure = 0.3
        else:
            temperature = 23.0
            pressure = 0.0

        return shot_seconds, f"{temperature:.1f} C", f"{pressure:.1f} bar"

    def _snapshot_locked(self) -> SimulatorSnapshot:
        shot_seconds, brew_temperature, pressure_bar = self._build_telemetry_locked()
        transport_snapshot: SerialLinkSnapshot = serial_link_manager.get_snapshot()

        important_data = {
            "Current State": self._current_state.value,
            "Client IP Address": self._client_ip_address or "Not set",
            "Connection Status": "Connected" if self._connected_to_client else "Disconnected",
            "Machine Status": "Initialized" if self._machine_initialized else "Not initialized",
            "Last Command": self._last_command,
            "Transport Handshake": transport_snapshot.handshake_state,
            "Protocol": transport_snapshot.protocol,
            "Shot Timer": f"{shot_seconds}s",
            "Brew Temperature": brew_temperature,
            "Pressure": pressure_bar,
            "Last Transition": self._last_transition_at,
        }

        return SimulatorSnapshot(
            current_state=self._current_state.value,
            client_ip_address=self._client_ip_address,
            last_transition_at=self._last_transition_at,
            machine_initialized=self._machine_initialized,
            connected_to_client=self._connected_to_client,
            brew_active=self._brew_active,
            shot_timer_seconds=shot_seconds,
            brew_temperature_c=brew_temperature,
            pressure_bar=pressure_bar,
            transport=asdict(transport_snapshot),
            important_data=important_data,
            logs=list(self._logs),
            available_states=[state.value for state in SimulatorState],
        )


simulator_runtime = SimulatorRuntime()
