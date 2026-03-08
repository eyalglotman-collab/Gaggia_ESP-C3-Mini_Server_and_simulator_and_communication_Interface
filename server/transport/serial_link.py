"""Serial transport ownership for the simulator."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from threading import Lock


@dataclass(slots=True)
class SerialLinkSnapshot:
    """@brief Describe the current simulator transport status.

    @details The simulator UI surfaces this information in the important-data
    panel so the operator can see whether the backend believes the client link
    is online.
    """

    connected: bool
    target_client_ip: str
    port_name: str
    protocol: str
    handshake_state: str
    last_event_at: str
    last_event: str


class SerialLinkManager:
    """@brief Own exactly one logical client link for the simulator runtime.

    @details The current implementation simulates the backend transport layer.
    It intentionally keeps one lock-guarded link owner so later serial or socket
    code has a single place to manage connection state.
    """

    def __init__(self, port: str | None = None) -> None:
        """@brief Initialize the transport manager.

        @details The simulator starts disconnected and can later be pointed at a
        client IP address from the UI.
        @param[in] port Optional logical port label for future transport work.
        """

        self._lock = Lock()
        self._port_name = port or "SIM-LINK-01"
        self._target_client_ip = ""
        self._connected = False
        self._protocol = "Simulated Serial Bridge"
        self._handshake_state = "Waiting"
        self._last_event_at = self._timestamp()
        self._last_event = "Transport manager ready."

    def _timestamp(self) -> str:
        """@brief Build a UTC timestamp string for transport events."""

        return datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%SZ")

    def configure_target(self, client_ip_address: str) -> SerialLinkSnapshot:
        """@brief Set the requested client target."""

        with self._lock:
            self._target_client_ip = client_ip_address.strip()
            self._handshake_state = "Target configured"
            self._last_event = f"Target client set to {self._target_client_ip}."
            self._last_event_at = self._timestamp()
            return self._snapshot_locked()

    def connect(self) -> SerialLinkSnapshot:
        """@brief Simulate connecting the transport to the configured target."""

        with self._lock:
            if not self._target_client_ip:
                raise ValueError("Client IP address must be configured before connecting.")

            self._connected = True
            self._handshake_state = "Connected"
            self._last_event = f"Transport connected to client {self._target_client_ip}."
            self._last_event_at = self._timestamp()
            return self._snapshot_locked()

    def disconnect(self) -> SerialLinkSnapshot:
        """@brief Simulate disconnecting the active link."""

        with self._lock:
            self._connected = False
            self._handshake_state = "Disconnected"
            self._last_event = "Transport disconnected."
            self._last_event_at = self._timestamp()
            return self._snapshot_locked()

    def get_snapshot(self) -> SerialLinkSnapshot:
        """@brief Return the current transport snapshot."""

        with self._lock:
            return self._snapshot_locked()

    def _snapshot_locked(self) -> SerialLinkSnapshot:
        """@brief Build a transport snapshot while the lock is held."""

        return SerialLinkSnapshot(
            connected=self._connected,
            target_client_ip=self._target_client_ip,
            port_name=self._port_name,
            protocol=self._protocol,
            handshake_state=self._handshake_state,
            last_event_at=self._last_event_at,
            last_event=self._last_event,
        )


serial_link_manager = SerialLinkManager()
