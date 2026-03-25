"""Serial transport ownership for the simulator transport template."""

from __future__ import annotations

import json
import os
import subprocess
import traceback
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
        self._logs: deque[str] = deque(maxlen=2000)
        self._port_name = port_name or os.getenv("SIM_SERIAL_PORT", "COM4")
        self._baud_rate = baud_rate
        self._read_timeout_sec = self._read_env_float("SIM_SERIAL_READ_TIMEOUT_SEC", 0.1, minimum=0.01)
        self._write_timeout_sec = self._read_env_float("SIM_SERIAL_WRITE_TIMEOUT_SEC", 1.5, minimum=0.05)
        self._tx_max_attempts = self._read_env_int("SIM_SERIAL_TX_MAX_ATTEMPTS", 3, minimum=1)
        self._tx_retry_backoff_sec = self._read_env_float("SIM_SERIAL_TX_RETRY_BACKOFF_SEC", 0.05, minimum=0.0)
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
        self._log(
            "Transport manager initialized for "
            f"{self._port_name} @ {self._baud_rate} "
            f"(read_timeout={self._read_timeout_sec:.2f}s, "
            f"write_timeout={self._write_timeout_sec:.2f}s, "
            f"tx_attempts={self._tx_max_attempts})."
        )

    @staticmethod
    def _read_env_float(name: str, default: float, *, minimum: float) -> float:
        raw = os.getenv(name)
        if raw is None:
            return default
        try:
            value = float(raw)
        except ValueError:
            return default
        return max(minimum, value)

    @staticmethod
    def _read_env_int(name: str, default: int, *, minimum: int) -> int:
        raw = os.getenv(name)
        if raw is None:
            return default
        try:
            value = int(raw)
        except ValueError:
            return default
        return max(minimum, value)

    def _timestamp(self) -> str:
        now = datetime.now(UTC)
        return f"{now:%H:%M:%S}.{now.microsecond // 10000:02d}"

    def _log(self, message: str) -> None:
        self._logs.appendleft(f"[{self._timestamp()}] {message}")

    def _set_event(self, message: str, error: str = "") -> None:
        self._last_event = message
        self._last_event_at = self._timestamp()
        self._last_error = error
        self._log(message if not error else f"{message} ({error})")

    @staticmethod
    def _try_parse_u32_payload_value(payload_text: str, key_text: str) -> int | None:
        """@brief Parse one unsigned integer key from a semicolon payload.

        @details KEEPALIVE payloads include `sid` and `req` metadata. The
        parser keeps serial-layer logging self-contained so operators can
        diagnose stale and duplicate frames without cross-referencing code.
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

    def _format_keepalive_metadata(self, frame: Frame) -> str:
        """@brief Build KEEPALIVE direction and correlation metadata text.

        @details The bridge sends `ka_req` and the client replies with
        `ka_resp`. Surfacing this at RX time removes ambiguity when sequence
        numbers appear close together in the monitor.
        """

        payload_text = frame.payload.decode("utf-8", errors="ignore").strip().lower()
        direction = "UNKNOWN"
        if payload_text.startswith("ka_req"):
            direction = "REQ"
        elif payload_text.startswith("ka_resp"):
            direction = "RESP"
        elif payload_text.startswith("keepalive"):
            direction = "LEGACY"

        sid = self._try_parse_u32_payload_value(payload_text, "sid")
        req = self._try_parse_u32_payload_value(payload_text, "req")

        metadata = f" dir={direction}"
        if sid is not None:
            metadata += f" sid={sid}"
        if req is not None:
            metadata += f" req={req}"
        return metadata

    def _detach_serial_locked(self, message: str, error: str = "") -> Any | None:
        """@brief Drop the current serial handle after an unrecoverable port fault.

        @details Flashing or resetting the ESP32-C3 can invalidate the current
        Windows COM handle. The simulator must stop using that stale handle and
        return the port to a closed state so the operator can reopen it cleanly.
        """

        current = self._serial
        self._reader_running = False
        self._serial = None
        self._reader_thread = None
        self._set_event(message, error)
        return current

    def configure_port(self, port_name: str) -> SerialLinkSnapshot:
        with self._lock:
            self._port_name = port_name.strip() or self._port_name
            self._set_event(f"Target serial port configured to {self._port_name}.")
            return self._snapshot_locked()

    def open_port(self) -> SerialLinkSnapshot:
        if serial is None:
            raise RuntimeError("pyserial is not installed in the simulator environment.")

        with self._lock:
            if self._serial is not None and getattr(self._serial, "is_open", False):
                self._set_event(f"Serial port {self._port_name} already open.")
                return self._snapshot_locked()
            port_name = self._port_name
            baud_rate = self._baud_rate
            read_timeout_sec = self._read_timeout_sec
            write_timeout_sec = self._write_timeout_sec

        opened_serial: Any | None = None
        try:
            if "://" in port_name:
                opened_serial = serial.serial_for_url(
                    port_name,
                    baud_rate,
                    timeout=read_timeout_sec,
                    write_timeout=write_timeout_sec,
                )
            else:
                opened_serial = serial.Serial(
                    port_name,
                    baud_rate,
                    timeout=read_timeout_sec,
                    write_timeout=write_timeout_sec,
                )
        except Exception as exc:  # pragma: no cover
            with self._lock:
                self._serial = None
                self._set_event(f"Failed to open serial port {port_name}.", str(exc))
            raise RuntimeError(str(exc)) from exc

        reader_thread = Thread(target=self._reader_loop, name="serial-link-rx", daemon=True)
        with self._lock:
            if self._serial is not None and getattr(self._serial, "is_open", False):
                try:
                    if opened_serial is not None and getattr(opened_serial, "is_open", False):
                        opened_serial.close()
                except Exception:
                    pass
                self._set_event(f"Serial port {self._port_name} already open.")
                return self._snapshot_locked()

            self._serial = opened_serial
            self._reader_running = True
            self._reader_thread = reader_thread
            self._set_event(f"Opened serial port {port_name} @ {baud_rate}.")
            snapshot = self._snapshot_locked()

        reader_thread.start()
        return snapshot

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

    def force_release_port(self) -> tuple[SerialLinkSnapshot, list[int]]:
        """@brief Close the local serial handle and hard-stop external holders.

        @details The simulator owns the port directly when it is open. This
        helper first closes the local handle, then uses Windows process
        command-line heuristics to find likely external tools still holding the
        same COM port and force-kills them.
        @return Current snapshot plus the list of external PIDs that were terminated.
        """

        self.close_port()
        released_pids = self._kill_external_port_holders()
        with self._lock:
            detail = ", ".join(str(pid) for pid in released_pids) if released_pids else "none"
            self._set_event(f"Hard COM release executed for {self._port_name}.")
            self._log(f"Forced COM release killed PIDs: {detail}.")
            return self._snapshot_locked(), released_pids

    def send_frame(self, frame: Frame) -> SerialLinkSnapshot:
        data = encode_frame(frame)
        for attempt in range(1, self._tx_max_attempts + 1):
            stale_serial: Any | None = None
            snapshot: SerialLinkSnapshot | None = None
            should_retry = False

            with self._lock:
                current = self._serial
                if current is None or not getattr(current, "is_open", False):
                    raise RuntimeError(f"Serial port {self._port_name} is not open.")
                port_name = self._port_name
                tx_max_attempts = self._tx_max_attempts
            try:
                current.write(data)
            except Exception as exc:
                with self._lock:
                    still_active = current is self._serial and bool(
                        self._serial is not None and getattr(self._serial, "is_open", False)
                    )
                    should_retry = (
                        still_active
                        and attempt < tx_max_attempts
                        and self._is_retryable_write_error(exc)
                    )
                    if should_retry:
                        self._set_event(
                            (
                                f"TX transient error on {port_name}; "
                                f"retrying ({attempt}/{tx_max_attempts - 1})."
                            ),
                            str(exc),
                        )
                    else:
                        if still_active:
                            stale_serial = self._detach_serial_locked(f"TX failed on {port_name}.", str(exc))
                        else:
                            self._set_event(f"TX failed on {port_name}.", str(exc))
                        snapshot = self._snapshot_locked()
            else:
                with self._lock:
                    self._tx_frames += 1
                    self._tx_bytes += len(data)
                    self._last_tx_at = self._timestamp()
                    self._set_event(
                        f"TX {frame.message_type.name} seq={frame.sequence} "
                        f"host={frame.host_live_integer} bytes={len(data)}"
                    )
                    if attempt > 1:
                        self._log(
                            f"TX recovered after {attempt} attempts on {port_name} "
                            f"for seq={frame.sequence}."
                        )
                    return self._snapshot_locked()

            if stale_serial is not None:
                try:
                    if getattr(stale_serial, "is_open", False):
                        stale_serial.close()
                except Exception:
                    pass
                if snapshot is not None:
                    raise RuntimeError(snapshot.last_error or f"Serial port {port_name} write failed.")
                raise RuntimeError(f"Serial port {port_name} write failed.")

            if should_retry:
                sleep(self._tx_retry_backoff_sec)
                continue

            break

        raise RuntimeError(f"Serial port {port_name} write failed.")

    def _is_retryable_write_error(self, exc: Exception) -> bool:
        if serial is not None:
            timeout_type = getattr(serial, "SerialTimeoutException", None)
            if timeout_type is not None and isinstance(exc, timeout_type):
                return True
        message = str(exc).lower()
        return "timeout" in message

    def pop_received_frames(self) -> list[Frame]:
        with self._lock:
            frames = list(self._rx_frames)
            self._rx_frames.clear()
            return frames

    def clear_buffers(self) -> None:
        """@brief Clear buffered RX data and queued frames.

        @details The reset path uses this helper to stop stale transport
        traffic from leaking into the next low-level controller session.
        """

        with self._lock:
            current = self._serial
            if current is not None and getattr(current, "is_open", False):
                try:
                    current.reset_input_buffer()
                except Exception:
                    pass
                try:
                    current.reset_output_buffer()
                except Exception:
                    pass
            self._rx_buffer.clear()
            self._rx_frames.clear()
            self._set_event("Cleared serial RX buffers.")

    def get_logs(self) -> list[str]:
        with self._lock:
            return list(self._logs)

    def get_snapshot(self) -> SerialLinkSnapshot:
        with self._lock:
            return self._snapshot_locked()

    def _kill_external_port_holders(self) -> list[int]:
        """@brief Force-stop likely external processes holding the COM port.

        @details Native Windows does not expose owning-PID lookup for COM ports
        directly in the standard library, so the simulator uses process
        command-line heuristics for the configured COM name and common serial
        tooling patterns such as monitor, esptool, and pyserial launches.
        @return List of terminated process IDs.
        """

        if os.name != "nt":
            return []

        port_name = self._port_name
        ps_command = (
            "$port = '{0}'; "
            "$regex = [regex]::Escape($port); "
            "$candidates = Get-CimInstance Win32_Process | "
            "Where-Object {{ $_.CommandLine -and "
            "(($_.CommandLine -match $regex) -or "
            "($_.CommandLine -match 'idf_monitor|esptool|monitor_capture|serial\\.Serial|serial_for_url|pyserial')) }} | "
            "Select-Object ProcessId, Name, CommandLine; "
            "$candidates | ConvertTo-Json -Compress"
        ).format(port_name.replace("'", "''"))

        try:
            result = subprocess.run(
                ["powershell.exe", "-NoProfile", "-Command", ps_command],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
        except Exception:
            return []

        stdout = result.stdout.strip()
        if not stdout:
            return []

        try:
            decoded = json.loads(stdout)
        except json.JSONDecodeError:
            return []

        entries = decoded if isinstance(decoded, list) else [decoded]
        released_pids: list[int] = []
        current_pid = os.getpid()

        for entry in entries:
            try:
                process_id = int(entry.get("ProcessId"))
            except Exception:
                continue
            if process_id == current_pid:
                continue

            kill_result = subprocess.run(
                ["taskkill", "/F", "/PID", str(process_id)],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            if kill_result.returncode == 0:
                released_pids.append(process_id)

        return released_pids

    def _reader_loop(self) -> None:
        try:
            self._reader_loop_inner()
        except Exception as exc:
            print(
                f"\n[serial-link-rx] UNHANDLED CRASH on {self._port_name}: {exc}",
                flush=True,
            )
            traceback.print_exc()
            with self._lock:
                self._detach_serial_locked(
                    f"Reader thread crashed on {self._port_name}.",
                    str(exc),
                )

    def _reader_loop_inner(self) -> None:
        while self._reader_running:
            current = self._serial
            if current is None:
                sleep(0.05)
                continue
            try:
                chunk = current.read(256)
            except Exception as exc:  # pragma: no cover
                stale_serial: Any | None = None
                with self._lock:
                    stale_serial = self._detach_serial_locked(
                        f"Serial read failed on {self._port_name}.",
                        str(exc),
                    )
                if stale_serial is not None:
                    try:
                        if getattr(stale_serial, "is_open", False):
                            stale_serial.close()
                    except Exception:
                        pass
                print(
                    f"[serial-link-rx] Read error on {self._port_name}: {exc}",
                    flush=True,
                )
                break
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
                    print(
                        f"[serial-link-rx] Frame decode error on {self._port_name}: {exc}",
                        flush=True,
                    )
                    self._set_event("RX frame decode error.", str(exc))
                    continue
                for frame in frames:
                    self._rx_frames.append(frame)
                    self._rx_frames_count += 1
                    keepalive_metadata = ""
                    if frame.message_type.name == "KEEPALIVE":
                        keepalive_metadata = self._format_keepalive_metadata(frame)
                    self._set_event(
                        f"RX {frame.message_type.name} seq={frame.sequence}{keepalive_metadata} "
                        f"server={frame.host_live_integer} device={frame.device_live_integer} bytes={len(frame.payload)}"
                    )

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
