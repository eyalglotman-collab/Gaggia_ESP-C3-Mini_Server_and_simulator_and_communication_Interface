"""Binary data payload channel for the Eyal Espresso simulator.

Server → Client (downlink):
    Sequential FIFO. A background thread generates one packet every configured
    packet interval (default 20 ms) and calls ``send_callback`` to deliver it
    over the active serial/TCP path. The sequence counter starts at zero on
    every ``start()`` call so the client can detect gaps.

Client → Server (uplink):
    Non-synchronized best-effort channel.  Incoming binary uplink frames are
    pushed to a bounded deque.  The application polls ``pop_uplink()`` to
    consume them; older entries are silently dropped when the deque is full.
"""

from __future__ import annotations

import math
import struct
import time
import traceback
from collections import deque
from threading import Event, Lock, Thread
from typing import Callable, Optional

# ---------------------------------------------------------------------------
# Size constants — must match data_payload.h on the client side.
# ---------------------------------------------------------------------------

DATA_SIZE_FLOATS: int = 100
DATA_SIZE_INT: int = 20
DATA_SIZE_STRING: int = 50

FIFO_DEPTH: int = 10

DATA_MAGIC_DOWNLINK: int = 0xD0
DATA_MAGIC_UPLINK: int = 0xD1

# ---------------------------------------------------------------------------
# Struct format strings (little-endian, packed).
#
# Downlink: magic(B) + seq(I) + floats(100f) + ints(20i) + string(50s)
# Uplink:   magic(B) + seq(I) + timestamp_ms(I) + floats(100f) + ints(20i) + string(50s)
# ---------------------------------------------------------------------------

_DL_FMT: str = f"<BI{DATA_SIZE_FLOATS}f{DATA_SIZE_INT}i{DATA_SIZE_STRING}s"
_UL_FMT: str = f"<BII{DATA_SIZE_FLOATS}f{DATA_SIZE_INT}i{DATA_SIZE_STRING}s"

DOWNLINK_PACKET_SIZE: int = struct.calcsize(_DL_FMT)
UPLINK_PACKET_SIZE: int = struct.calcsize(_UL_FMT)

DEFAULT_SINE_AMPLITUDE: float = 1.0
DEFAULT_SINE_FREQUENCY_HZ: float = 1.0
DEFAULT_PACKET_INTERVAL_MS: int = 20
DEFAULT_BREW_PACKET_INTERVAL_MS: int = 100
MIN_PACKET_INTERVAL_MS: int = 10
MAX_PACKET_INTERVAL_MS: int = 200
SIM_FLOAT_BYTES_PER_PACKET: int = DATA_SIZE_FLOATS * 4
BREW_SAMPLES_PER_CHANNEL: int = 10
DEFAULT_BREW_PROFILE_ID: int = 1
DEFAULT_BREW_PROFILE_NAME: str = "Classic 9 Bar"
DEFAULT_BREW_TIME_SEC: float = 30.0
DEFAULT_BREW_TARGET_PRESSURE_BAR: float = 9.0
DEFAULT_BREW_TARGET_FLOW_ML_SEC: float = 2.2
DEFAULT_BREW_TARGET_TEMPERATURE_C: float = 93.0


def _compute_simulation_throughput_kbytes_per_sec(packet_interval_ms: int) -> float:
    """@brief Compute float-channel throughput for the configured packet interval.

    @details Throughput is based on the simulator's float payload channel:
    ``DATA_SIZE_FLOATS * 4 bytes`` sent once per packet interval.
    """

    safe_interval_ms = max(MIN_PACKET_INTERVAL_MS, min(int(packet_interval_ms), MAX_PACKET_INTERVAL_MS))
    packets_per_second = 1000.0 / float(safe_interval_ms)
    bytes_per_second = float(SIM_FLOAT_BYTES_PER_PACKET) * packets_per_second
    return bytes_per_second / 1000.0


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------

def encode_downlink(seq: int, floats: list[float], ints: list[int], text: str) -> bytes:
    """@brief Encode one downlink packet to bytes.

    @details Pads or truncates ``floats``, ``ints``, and ``text`` to the
    declared sizes so the caller does not need to manage alignment.

    @param seq    Server-side sequence counter.
    @param floats Float array (padded with 0.0 to DATA_SIZE_FLOATS).
    @param ints   Integer array (padded with 0 to DATA_SIZE_INT).
    @param text   Status string (truncated / null-padded to DATA_SIZE_STRING).
    @return Packed bytes of length DOWNLINK_PACKET_SIZE.
    """
    f_pad = (list(floats) + [0.0] * DATA_SIZE_FLOATS)[:DATA_SIZE_FLOATS]
    i_pad = (list(ints) + [0] * DATA_SIZE_INT)[:DATA_SIZE_INT]
    s_enc = text.encode("utf-8", errors="replace")[:DATA_SIZE_STRING].ljust(DATA_SIZE_STRING, b"\x00")
    return struct.pack(_DL_FMT, DATA_MAGIC_DOWNLINK, seq & 0xFFFFFFFF, *f_pad, *i_pad, s_enc)


def decode_uplink(payload: bytes) -> Optional[dict]:
    """@brief Decode a raw uplink payload bytes to a dict.

    @details Returns ``None`` if the payload is not a valid uplink packet.
    The returned dict has keys: ``magic``, ``seq``, ``timestamp_ms``,
    ``f`` (list[float]), ``i`` (list[int]), ``s`` (str).

    @param payload Raw bytes received in a DATA frame.
    @return Decoded packet dict or None.
    """
    if len(payload) != UPLINK_PACKET_SIZE:
        return None
    if payload[0] != DATA_MAGIC_UPLINK:
        return None
    try:
        fields = struct.unpack(_UL_FMT, payload)
    except struct.error:
        return None
    idx = 0
    magic = fields[idx]; idx += 1
    seq = fields[idx]; idx += 1
    timestamp_ms = fields[idx]; idx += 1
    f_vals = list(fields[idx: idx + DATA_SIZE_FLOATS]); idx += DATA_SIZE_FLOATS
    i_vals = list(fields[idx: idx + DATA_SIZE_INT]); idx += DATA_SIZE_INT
    s_raw: bytes = fields[idx]
    return {
        "magic": magic,
        "seq": seq,
        "timestamp_ms": timestamp_ms,
        "f": f_vals,
        "i": i_vals,
        "s": s_raw.rstrip(b"\x00").decode("utf-8", errors="replace"),
    }


# ---------------------------------------------------------------------------
# DataPayloadManager
# ---------------------------------------------------------------------------

class DataPayloadManager:
    """@brief Owns the downlink send thread and the uplink receive buffer.

    @details A single module-level instance ``data_payload_manager`` is
    created at the bottom of this file.  ``link_state_machine.py`` imports
    it and calls ``start()`` / ``stop()`` around the active transport
    session, and ``receive_uplink()`` for each incoming uplink DATA frame.
    """

    def __init__(self) -> None:
        self._lock = Lock()
        self._ul_fifo: deque[dict] = deque(maxlen=FIFO_DEPTH)
        self._dl_seq: int = 0
        self._ul_rx_count: int = 0
        self._ul_drop_count: int = 0
        self._dl_tx_count: int = 0
        self._last_ul_seq: int = -1
        self._simulation_enabled: bool = False
        self._sine_amplitude: float = DEFAULT_SINE_AMPLITUDE
        self._sine_frequency_hz: float = DEFAULT_SINE_FREQUENCY_HZ
        self._packet_interval_ms: int = DEFAULT_PACKET_INTERVAL_MS
        self._sine_phase_rad: float = 0.0
        self._selected_profile_id: int = DEFAULT_BREW_PROFILE_ID
        self._selected_profile_name: str = DEFAULT_BREW_PROFILE_NAME
        self._brew_target_pressure_bar: float = DEFAULT_BREW_TARGET_PRESSURE_BAR
        self._brew_target_flow_ml_sec: float = DEFAULT_BREW_TARGET_FLOW_ML_SEC
        self._brew_target_temperature_c: float = DEFAULT_BREW_TARGET_TEMPERATURE_C
        self._brew_time_sec: float = DEFAULT_BREW_TIME_SEC
        self._brew_active: bool = False
        self._brew_started_monotonic: float = 0.0
        self._brew_elapsed_sec: float = 0.0
        self._last_control_event: str = "Idle"
        self._send_callback: Optional[Callable[[bytes], None]] = None
        self._stop_event = Event()
        self._thread: Optional[Thread] = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self, send_callback: Callable[[bytes], None]) -> None:
        """@brief Start the periodic downlink send thread.

        @details ``send_callback(payload_bytes)`` is called from the
        background thread at the currently configured packet interval when the
        session is active. The caller (``link_state_machine``) should wrap the
        callback with the appropriate lock and state guard.

        @param send_callback Callable that sends one DATA frame payload.
        """
        self.stop()
        self._send_callback = send_callback
        self._dl_seq = 0
        self._simulation_enabled = False
        self._sine_phase_rad = 0.0
        self._brew_active = False
        self._brew_started_monotonic = 0.0
        self._brew_elapsed_sec = 0.0
        self._last_control_event = "Idle"
        self._stop_event.clear()
        self._thread = Thread(target=self._run, daemon=True, name="data_payload_dl")
        self._thread.start()

    def stop(self) -> None:
        """@brief Stop the downlink send thread and clear pending state."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            self._thread = None
        with self._lock:
            self._ul_fifo.clear()
            self._dl_seq = 0
            self._simulation_enabled = False
            self._sine_phase_rad = 0.0
            self._brew_active = False
            self._brew_started_monotonic = 0.0
            self._brew_elapsed_sec = 0.0

    # ------------------------------------------------------------------
    # Simulation control
    # ------------------------------------------------------------------

    def set_simulation_enabled(self, enabled: bool) -> None:
        """@brief Enable or disable sine-wave downlink generation."""
        with self._lock:
            self._simulation_enabled = bool(enabled)
            if enabled:
                self._brew_active = False
                self._brew_started_monotonic = 0.0
                self._brew_elapsed_sec = 0.0
                self._last_control_event = "DataSimulationOn"
            else:
                self._last_control_event = "DataSimulationOFF"

    def configure_sine(
        self,
        *,
        amplitude: float | None = None,
        frequency_hz: float | None = None,
        packet_interval_ms: int | None = None,
    ) -> None:
        """@brief Update sine generator amplitude/frequency/interval parameters.

        @details Parameters are clamped to safe positive ranges so malformed
        command payloads cannot create invalid values in the generator.

        @param amplitude    Optional new sine amplitude.
        @param frequency_hz Optional new sine frequency in Hz.
        @param packet_interval_ms Optional packet interval in milliseconds.
        """
        with self._lock:
            if amplitude is not None:
                self._sine_amplitude = max(0.0, min(float(amplitude), 1000.0))
            if frequency_hz is not None:
                self._sine_frequency_hz = max(0.01, min(float(frequency_hz), 1000.0))
            if packet_interval_ms is not None:
                self._packet_interval_ms = max(
                    MIN_PACKET_INTERVAL_MS,
                    min(int(packet_interval_ms), MAX_PACKET_INTERVAL_MS),
                )

    def select_profile(self, profile_id: int | None = None, *, offline: bool | None = None) -> None:
        """@brief Select active brew profile metadata for the next StartBrew.

        @details The simulator currently exposes one profile. The payload still
        records profile metadata so the transport contract supports expansion.
        """

        del offline
        with self._lock:
            _ = profile_id  # reserved for future profile table expansion
            self._selected_profile_id = DEFAULT_BREW_PROFILE_ID
            self._selected_profile_name = DEFAULT_BREW_PROFILE_NAME
            self._brew_target_pressure_bar = DEFAULT_BREW_TARGET_PRESSURE_BAR
            self._brew_target_flow_ml_sec = DEFAULT_BREW_TARGET_FLOW_ML_SEC
            self._brew_target_temperature_c = DEFAULT_BREW_TARGET_TEMPERATURE_C
            self._brew_time_sec = DEFAULT_BREW_TIME_SEC
            self._last_control_event = "ProfileSelection"

    def start_brew(self, profile_id: int | None = None, packet_interval_ms: int | None = None) -> None:
        """@brief Start brew-mode downlink packet generation."""

        with self._lock:
            _ = profile_id  # reserved for future profile table expansion
            self._selected_profile_id = DEFAULT_BREW_PROFILE_ID
            self._selected_profile_name = DEFAULT_BREW_PROFILE_NAME
            self._brew_target_pressure_bar = DEFAULT_BREW_TARGET_PRESSURE_BAR
            self._brew_target_flow_ml_sec = DEFAULT_BREW_TARGET_FLOW_ML_SEC
            self._brew_target_temperature_c = DEFAULT_BREW_TARGET_TEMPERATURE_C
            self._brew_time_sec = DEFAULT_BREW_TIME_SEC
            self._packet_interval_ms = max(
                MIN_PACKET_INTERVAL_MS,
                min(
                    int(DEFAULT_BREW_PACKET_INTERVAL_MS if packet_interval_ms is None else packet_interval_ms),
                    MAX_PACKET_INTERVAL_MS,
                ),
            )
            self._brew_active = True
            self._brew_started_monotonic = time.monotonic()
            self._brew_elapsed_sec = 0.0
            self._simulation_enabled = False
            self._last_control_event = "StartBrew"

    def stop_brew(self) -> None:
        """@brief Stop brew-mode packet generation."""

        with self._lock:
            self._brew_active = False
            self._brew_started_monotonic = 0.0
            self._brew_elapsed_sec = 0.0
            self._last_control_event = "StopBrew"

    # ------------------------------------------------------------------
    # Downlink send thread
    # ------------------------------------------------------------------

    def _run(self) -> None:
        """@brief Background send loop paced by packet interval."""
        try:
            while not self._stop_event.is_set():
                with self._lock:
                    interval_ms = self._packet_interval_ms
                interval_s = max(MIN_PACKET_INTERVAL_MS, min(interval_ms, MAX_PACKET_INTERVAL_MS)) / 1000.0
                if self._stop_event.wait(timeout=interval_s):
                    break
                self._generate_and_send(interval_s)
        except Exception as exc:
            print(f"\n[data-payload-dl] UNHANDLED CRASH: {exc}", flush=True)
            traceback.print_exc()

    def _build_brew_channels(
        self,
        elapsed_sec: float,
        sample_dt_sec: float,
        *,
        target_pressure_bar: float,
        target_flow_ml_sec: float,
        target_temperature_c: float,
        brew_time_sec: float,
    ) -> tuple[list[float], list[float], list[float]]:
        """@brief Synthesize pressure/flow/temperature channels for brew mode."""

        pressure_samples: list[float] = []
        flow_samples: list[float] = []
        temperature_samples: list[float] = []
        safe_brew_time = max(0.001, brew_time_sec)

        for sample_index in range(BREW_SAMPLES_PER_CHANNEL):
            t_sec = elapsed_sec + (sample_dt_sec * float(sample_index))
            progress = max(0.0, min(t_sec / safe_brew_time, 1.0))

            if t_sec < 4.0:
                pressure_bar = target_pressure_bar * (t_sec / 4.0)
            elif progress > 0.92:
                tail = max(0.0, 1.0 - ((progress - 0.92) / 0.08))
                pressure_bar = (target_pressure_bar * 0.9 * tail) + (0.10 * math.sin(2.0 * math.pi * 1.1 * t_sec))
            else:
                pressure_bar = target_pressure_bar + (0.18 * math.sin(2.0 * math.pi * 0.8 * t_sec))
            pressure_bar = max(0.0, pressure_bar)

            if t_sec < 6.0:
                flow_ml_sec = target_flow_ml_sec * (t_sec / 6.0)
            elif progress > 0.92:
                tail = max(0.0, 1.0 - ((progress - 0.92) / 0.08))
                flow_ml_sec = (target_flow_ml_sec * 0.85 * tail) + (0.08 * math.sin(2.0 * math.pi * 0.7 * t_sec))
            else:
                flow_ml_sec = target_flow_ml_sec + (0.12 * math.sin(2.0 * math.pi * 0.55 * t_sec + 0.6))
            flow_ml_sec = max(0.0, flow_ml_sec)

            temperature_c = target_temperature_c - (1.6 * math.exp(-t_sec / 7.0)) + (0.06 * math.sin(2.0 * math.pi * 0.2 * t_sec))

            pressure_samples.append(float(pressure_bar))
            flow_samples.append(float(flow_ml_sec))
            temperature_samples.append(float(temperature_c))

        return pressure_samples, flow_samples, temperature_samples

    def _generate_and_send(self, packet_interval_s: float) -> None:
        """@brief Build one downlink packet from active stream mode and send it."""
        cb = self._send_callback
        if cb is None:
            return

        brew_mode = False
        seq = 0
        packet_interval_ms = DEFAULT_PACKET_INTERVAL_MS
        amplitude = 0.0
        frequency_hz = 0.0
        phase_rad = 0.0
        phase_step = 0.0
        brew_elapsed_sec = 0.0
        brew_time_sec = DEFAULT_BREW_TIME_SEC
        brew_profile_id = DEFAULT_BREW_PROFILE_ID
        brew_profile_name = DEFAULT_BREW_PROFILE_NAME
        brew_target_pressure_bar = DEFAULT_BREW_TARGET_PRESSURE_BAR
        brew_target_flow_ml_sec = DEFAULT_BREW_TARGET_FLOW_ML_SEC
        brew_target_temperature_c = DEFAULT_BREW_TARGET_TEMPERATURE_C

        with self._lock:
            if not self._simulation_enabled and not self._brew_active:
                return

            seq = self._dl_seq
            self._dl_seq = (self._dl_seq + 1) & 0xFFFFFFFF
            packet_interval_ms = self._packet_interval_ms

            if self._brew_active:
                brew_mode = True
                now_monotonic = time.monotonic()
                brew_elapsed_sec = max(
                    0.0,
                    now_monotonic - self._brew_started_monotonic if self._brew_started_monotonic > 0.0 else 0.0,
                )
                brew_time_sec = self._brew_time_sec
                if brew_elapsed_sec >= brew_time_sec:
                    self._brew_active = False
                    self._brew_elapsed_sec = brew_time_sec
                    self._brew_started_monotonic = 0.0
                    self._last_control_event = "BrewComplete"
                    return
                brew_profile_id = self._selected_profile_id
                brew_profile_name = self._selected_profile_name
                brew_target_pressure_bar = self._brew_target_pressure_bar
                brew_target_flow_ml_sec = self._brew_target_flow_ml_sec
                brew_target_temperature_c = self._brew_target_temperature_c
            else:
                amplitude = self._sine_amplitude
                frequency_hz = self._sine_frequency_hz
                phase_rad = self._sine_phase_rad
                sample_rate_hz = float(DATA_SIZE_FLOATS) / max(packet_interval_s, 0.001)
                phase_step = (2.0 * math.pi * frequency_hz) / sample_rate_hz
                self._sine_phase_rad = (
                    phase_rad + (phase_step * float(DATA_SIZE_FLOATS))
                ) % (2.0 * math.pi)

        if brew_mode:
            sample_dt_sec = packet_interval_s / float(BREW_SAMPLES_PER_CHANNEL)
            pressure_values, flow_values, temperature_values = self._build_brew_channels(
                brew_elapsed_sec,
                sample_dt_sec,
                target_pressure_bar=brew_target_pressure_bar,
                target_flow_ml_sec=brew_target_flow_ml_sec,
                target_temperature_c=brew_target_temperature_c,
                brew_time_sec=brew_time_sec,
            )
            floats = [0.0] * DATA_SIZE_FLOATS
            for sample_index in range(BREW_SAMPLES_PER_CHANNEL):
                floats[sample_index] = pressure_values[sample_index]
                floats[BREW_SAMPLES_PER_CHANNEL + sample_index] = flow_values[sample_index]
                floats[(2 * BREW_SAMPLES_PER_CHANNEL) + sample_index] = temperature_values[sample_index]
            ints = [
                seq & 0x7FFFFFFF,
                int(brew_profile_id),
                int(round(brew_elapsed_sec * 1000.0)),
                int(round(brew_time_sec * 1000.0)),
                int(round(brew_target_pressure_bar * 1000.0)),
                int(round(brew_target_flow_ml_sec * 1000.0)),
                int(round(brew_target_temperature_c * 1000.0)),
            ] + [0] * (DATA_SIZE_INT - 7)
            text = (
                f"brew=on;profile={brew_profile_id};name={brew_profile_name};"
                f"elapsed_s={brew_elapsed_sec:.2f};brew_time_s={brew_time_sec:.1f};"
                f"int_ms={packet_interval_ms};seq={seq}"
            )
        else:
            # Fill all float fields with a time-domain sine sampled over one packet.
            floats = [
                float(amplitude * math.sin(phase_rad + (phase_step * sample_index)))
                for sample_index in range(DATA_SIZE_FLOATS)
            ]
            ints = [
                seq & 0x7FFFFFFF,
                int(round(amplitude * 1000.0)),
                int(round(frequency_hz * 1000.0)),
            ] + [0] * (DATA_SIZE_INT - 3)
            text = (
                f"sim=on;amp={amplitude:.3f};freq={frequency_hz:.3f};"
                f"int_ms={packet_interval_ms};seq={seq}"
            )

        payload = encode_downlink(seq, floats, ints, text)
        try:
            cb(payload)
            with self._lock:
                self._dl_tx_count += 1
                if brew_mode:
                    self._brew_elapsed_sec = brew_elapsed_sec
        except Exception:
            pass  # link not ready; packet silently dropped

    # ------------------------------------------------------------------
    # Uplink receive
    # ------------------------------------------------------------------

    def receive_uplink(self, payload: bytes) -> None:
        """@brief Push one raw uplink payload into the receive FIFO.

        @details Called by ``link_state_machine`` when a DATA frame whose
        first byte is DATA_MAGIC_UPLINK arrives.  The deque is bounded so
        the oldest entry is dropped automatically when full.

        @param payload Raw bytes from the DATA frame payload field.
        """
        pkt = decode_uplink(payload)
        if pkt is None:
            return
        with self._lock:
            if len(self._ul_fifo) >= FIFO_DEPTH:
                self._ul_drop_count += 1
            self._ul_fifo.append(pkt)
            self._ul_rx_count += 1
            self._last_ul_seq = pkt["seq"]

    def pop_uplink(self) -> Optional[dict]:
        """@brief Pop the oldest received uplink packet, or None if empty.

        @details Called by the application or API layer to consume client
        sensor data.

        @return Packet dict with keys ``seq``, ``timestamp_ms``, ``f``,
                ``i``, ``s``, or ``None`` if the FIFO is empty.
        """
        with self._lock:
            return self._ul_fifo.popleft() if self._ul_fifo else None

    def get_stats(self) -> dict:
        """@brief Return a snapshot of data payload counters for the UI.

        @return Dict with keys ``dl_tx_count``, ``ul_rx_count``,
                ``ul_drop_count``, ``ul_fifo_depth``, ``dl_seq``,
                ``last_ul_seq``, ``sim_enabled``, ``sim_amplitude``,
                ``sim_frequency_hz``, ``sim_packet_interval_ms``,
                ``sim_data_throughput_kbytes_per_sec``, ``brew_active``,
                ``brew_profile_id``, ``brew_profile_name``, ``brew_elapsed_sec``,
                ``brew_time_sec``, ``brew_remaining_sec``.
        """
        with self._lock:
            throughput_kbytes_per_sec = _compute_simulation_throughput_kbytes_per_sec(self._packet_interval_ms)
            brew_elapsed_sec = self._brew_elapsed_sec
            if self._brew_active and self._brew_started_monotonic > 0.0:
                brew_elapsed_sec = max(0.0, time.monotonic() - self._brew_started_monotonic)
            brew_remaining_sec = max(0.0, self._brew_time_sec - brew_elapsed_sec)
            return {
                "dl_tx_count": self._dl_tx_count,
                "ul_rx_count": self._ul_rx_count,
                "ul_drop_count": self._ul_drop_count,
                "ul_fifo_depth": len(self._ul_fifo),
                "dl_seq": self._dl_seq,
                "last_ul_seq": self._last_ul_seq,
                "sim_enabled": self._simulation_enabled,
                "sim_amplitude": self._sine_amplitude,
                "sim_frequency_hz": self._sine_frequency_hz,
                "sim_packet_interval_ms": self._packet_interval_ms,
                "sim_data_throughput_kbytes_per_sec": throughput_kbytes_per_sec,
                "brew_active": self._brew_active,
                "brew_profile_id": self._selected_profile_id,
                "brew_profile_name": self._selected_profile_name,
                "brew_elapsed_sec": brew_elapsed_sec,
                "brew_time_sec": self._brew_time_sec,
                "brew_remaining_sec": brew_remaining_sec,
                "brew_samples_per_channel": BREW_SAMPLES_PER_CHANNEL,
                "last_control_event": self._last_control_event,
            }


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

data_payload_manager = DataPayloadManager()
