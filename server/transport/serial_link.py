"""Serial transport ownership for the simulator transport template."""

from __future__ import annotations

import os
from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime
from threading import Lock, Thread
from time import sleep
from typing import Any

from server.transport.frame_codec import Frame, FrameDecodeError, decode_frames, encode_frame

try:
    import serial  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover
    serial = None


@dataclass(slots=True)
class SerialLinkSnapshot:
    port_name: str
    baud_rate: int
    port_open: bool
    protocol: str
    last_event_at: str
    last_event: str
    last_error: str
    last_tx_at: str
    last_rx_at: str
    tx_frames: int
    rx_frames: int
    tx_bytes: int
    rx_bytes: int


class SerialLinkManager:
    """@brief Own one serial endpoint for the simulator host.

    @details The manager is intentionally the only code path allowed to touch
    the serial object. It exposes snapshots, logs, and queued RX frames to the
    low-level state machine above it.
    """

    def __init__(self, port_name: str | None = None, baud_rate: int = 115200) -> None:
        self._lock = Lock()
        self._serial: Any | None = None
        self._reader_thread: Thread | None = None
        self._reader_running = False
        self._rx_buffer = bytearray()
        self._rx_frames: deque[Frame] = deque()
        self._logs: deque[str] = deque(maxlen=400)
        self._port_name = port_name or os.getenv("SIM_SERIAL_PORT", "COM4")
        self._baud_rate = baud_rate
        self._protocol = "ESP32-C3 Framed Serial Link"
        self._last_event_at = self._timestamp()
        self._last_event = "Serial transport manager ready."
        self._last_error = ""
        self._last_tx_at = "Never"
        self._last_rx_at = "Never"
        self._tx_frames = 0
        self._rx_frames_count = 0
        self._tx_bytes = 0
        self._rx_bytes = 0
        self._log(f"Transport manager initialized for {self._port_name} @ {self._baud_rate}.")

    def _timestamp(self) -> str:
        return datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%SZ")

    def _log(self, message: str) -> None:
        self._logs.append(f"[{self._timestamp()}] {message}")

    def _set_event(self, message: str, error: str = "") -> None:
        self._last_event = message
        self._last_event_at = self._timestamp()
        self._last_error = error
        self._log(message if not error else f"{message} ({error})")

    def configure_port(self, port_name: str) -> SerialLinkSnapshot:
        with self._lock:
            self._port_name = port_name.strip() or self._port_name
            self._set_event(f"Target serial port configured to {self._port_name}.")
            return self._snapshot_locked()

    def open_port(self) -> SerialLinkSnapshot:
        with self._lock:
            if serial is None:
                raise RuntimeError("pyserial is not installed in the simulator environment.")
            if self._serial is not None and getattr(self._serial, "is_open", False):
                self._set_event(f"Serial port {self._port_name} already open.")
                return self._snapshot_locked()
            try:
                if "://" in self._port_name:
                    self._serial = serial.serial_for_url(
                        self._port_name,
                        self._baud_rate,
                        timeout=0.1,
                        write_timeout=0.25,
                    )
                else:
                    self._serial = serial.Serial(
                        self._port_name,
                        self._baud_rate,
                        timeout=0.1,
                        write_timeout=0.25,
                    )
            except Exception as exc:  # pragma: no cover
                self._serial = None
                self._set_event(f"Failed to open serial port {self._port_name}.", str(exc))
                raise RuntimeError(str(exc)) from exc
            self._reader_running = True
            self._reader_thread = Thread(target=self._reader_loop, name="serial-link-rx", daemon=True)
            self._reader_thread.start()
            self._set_event(f"Opened serial port {self._port_name} @ {self._baud_rate}.")
            return self._snapshot_locked()

    def close_port(self) -> SerialLinkSnapshot:
        with self._lock:
            self._reader_running = False
            current = self._serial
            current_reader = self._reader_thread
            self._serial = None
            self._reader_thread = None
        if current is not None:
            try:
                if getattr(current, "is_open", False):
                    current.close()
            except Exception:
                pass
        if current_reader is not None and current_reader.is_alive():
            current_reader.join(timeout=0.3)
        with self._lock:
            self._set_event(f"Closed serial port {self._port_name}.")
            return self._snapshot_locked()

    def send_frame(self, frame: Frame) -> SerialLinkSnapshot:
        data = encode_frame(frame)
        with self._lock:
            current = self._serial
            if current is None or not getattr(current, "is_open", False):
                raise RuntimeError(f"Serial port {self._port_name} is not open.")
            try:
                current.write(data)
            except Exception as exc:
                self._set_event(f"TX failed on {self._port_name}.", str(exc))
                raise RuntimeError(str(exc)) from exc
            self._tx_frames += 1
            self._tx_bytes += len(data)
            self._last_tx_at = self._timestamp()
            self._set_event(f"TX {frame.message_type.name} seq={frame.sequence} host={frame.host_live_integer} bytes={len(data)}")
            return self._snapshot_locked()

    def pop_received_frames(self) -> list[Frame]:
        with self._lock:
            frames = list(self._rx_frames)
            self._rx_frames.clear()
            return frames

    def get_logs(self) -> list[str]:
        with self._lock:
            return list(self._logs)

    def get_snapshot(self) -> SerialLinkSnapshot:
        with self._lock:
            return self._snapshot_locked()

    def _reader_loop(self) -> None:
        while self._reader_running:
            current = self._serial
            if current is None:
                sleep(0.05)
                continue
            try:
                chunk = current.read(256)
            except Exception as exc:  # pragma: no cover
                with self._lock:
                    self._set_event(f"Serial read failed on {self._port_name}.", str(exc))
                sleep(0.1)
                continue
            if not chunk:
                sleep(0.02)
                continue
            with self._lock:
                self._rx_bytes += len(chunk)
                self._rx_buffer.extend(chunk)
                self._last_rx_at = self._timestamp()
                try:
                    frames = decode_frames(self._rx_buffer)
                except FrameDecodeError as exc:
                    self._set_event("RX frame decode error.", str(exc))
                    continue
                for frame in frames:
                    self._rx_frames.append(frame)
                    self._rx_frames_count += 1
                    self._set_event(f"RX {frame.message_type.name} seq={frame.sequence} device={frame.device_live_integer} bytes={len(frame.payload)}")

    def _snapshot_locked(self) -> SerialLinkSnapshot:
        return SerialLinkSnapshot(
            port_name=self._port_name,
            baud_rate=self._baud_rate,
            port_open=bool(self._serial is not None and getattr(self._serial, "is_open", False)),
            protocol=self._protocol,
            last_event_at=self._last_event_at,
            last_event=self._last_event,
            last_error=self._last_error,
            last_tx_at=self._last_tx_at,
            last_rx_at=self._last_rx_at,
            tx_frames=self._tx_frames,
            rx_frames=self._rx_frames_count,
            tx_bytes=self._tx_bytes,
            rx_bytes=self._rx_bytes,
        )


serial_link_manager = SerialLinkManager()
