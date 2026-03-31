"""Binary data payload channel for the Eyal Espresso simulator.

Server → Client (downlink):
    Sequential FIFO. A background thread generates one packet every configured
    packet interval (default 1000 ms) and calls ``send_callback`` to deliver it
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

from server.communication.data_structures_lcd_controller import (
    LCDControllerBrewHomeState,
    brew_home_state_to_legacy_int_slots,
)

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
DEFAULT_PACKET_INTERVAL_MS: int = 1000
DEFAULT_BREW_PACKET_INTERVAL_MS: int = 100
MIN_PACKET_INTERVAL_MS: int = 10
MAX_PACKET_INTERVAL_MS: int = 5000
SIM_FLOAT_BYTES_PER_PACKET: int = DATA_SIZE_FLOATS * 4
BREW_SAMPLES_PER_CHANNEL: int = 10
DEFAULT_BREW_PROFILE_ID: int = 1
DEFAULT_BREW_PROFILE_NAME: str = "Classic 9 Bar"
DEFAULT_BREW_TIME_SEC: float = 30.0
DEFAULT_BREW_TARGET_PRESSURE_BAR: float = 9.0
DEFAULT_BREW_TARGET_FLOW_ML_SEC: float = 2.2
DEFAULT_BREW_TARGET_TEMPERATURE_C: float = 93.0
DEFAULT_HOME_WATER_LEVEL_PCT: float = 92.0
DEFAULT_HOME_WEIGHT_G: float = 0.0
DEFAULT_HOME_SHOT_TARGET_G: float = 36.0

PROFILE_PRESETS: tuple[dict[str, object], ...] = (
    {
        "id": 1,
        "name": "Classic 9 Bar",
        "target_temperature_c": 93.0,
        "target_pressure_bar": 9.0,
        "target_flow_ml_sec": 2.2,
        "preinfusion_seconds": 4.0,
        "shot_target_g": 36.0,
    },
    {
        "id": 2,
        "name": "Turbo Shot",
        "target_temperature_c": 91.0,
        "target_pressure_bar": 7.5,
        "target_flow_ml_sec": 3.0,
        "preinfusion_seconds": 2.0,
        "shot_target_g": 30.0,
    },
    {
        "id": 3,
        "name": "Light Roast",
        "target_temperature_c": 96.0,
        "target_pressure_bar": 9.5,
        "target_flow_ml_sec": 1.8,
        "preinfusion_seconds": 6.0,
        "shot_target_g": 40.0,
    },
)

BREW_PROFILE_SAMPLE_DT_SEC: float = 0.1
BREW_PROFILE_SAMPLE_COUNT: int = int(DEFAULT_BREW_TIME_SEC / BREW_PROFILE_SAMPLE_DT_SEC) + 1


def _build_profile_sample_library() -> dict[int, dict[str, list[float]]]:
    """@brief Precompute deterministic brew samples for each profile in static memory."""

    library: dict[int, dict[str, list[float]]] = {}
    sample_count = max(2, BREW_PROFILE_SAMPLE_COUNT)

    for profile in PROFILE_PRESETS:
        profile_id = int(profile.get("id", DEFAULT_BREW_PROFILE_ID))
        target_pressure = float(profile.get("target_pressure_bar", DEFAULT_BREW_TARGET_PRESSURE_BAR))
        target_flow = float(profile.get("target_flow_ml_sec", DEFAULT_BREW_TARGET_FLOW_ML_SEC))
        target_temp = float(profile.get("target_temperature_c", DEFAULT_BREW_TARGET_TEMPERATURE_C))
        shot_target = float(profile.get("shot_target_g", DEFAULT_HOME_SHOT_TARGET_G))
        temp_start = max(20.0, target_temp - 8.0)

        pressure_samples: list[float] = []
        flow_samples: list[float] = []
        temperature_samples: list[float] = []
        weight_samples: list[float] = []

        for index in range(sample_count):
            progress = float(index) / float(sample_count - 1)
            pressure_samples.append(float(max(0.0, target_pressure * progress)))
            flow_samples.append(float(max(0.0, target_flow * progress)))
            temperature_samples.append(float(temp_start + ((target_temp - temp_start) * progress)))
            weight_samples.append(float(max(0.0, shot_target * progress)))

        if pressure_samples:
            pressure_samples[-1] = float(max(0.0, target_pressure))
        if flow_samples:
            flow_samples[-1] = float(max(0.0, target_flow))
        if temperature_samples:
            temperature_samples[-1] = float(target_temp)
        if weight_samples:
            weight_samples[-1] = max(0.0, shot_target)

        library[profile_id] = {
            "pressure_bar": pressure_samples,
            "flow_ml_s": flow_samples,
            "temperature_c": temperature_samples,
            "weight_g": weight_samples,
        }

    return library


PROFILE_SAMPLE_LIBRARY: dict[int, dict[str, list[float]]] = _build_profile_sample_library()


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
        self._profile_presets: list[dict[str, object]] = [dict(profile) for profile in PROFILE_PRESETS]
        self._profile_sample_library: dict[int, dict[str, list[float]]] = PROFILE_SAMPLE_LIBRARY
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
        self._home_started_monotonic: float = time.monotonic()
        self._home_temperature_override_c: float | None = None
        self._home_water_level_override_pct: float | None = None
        self._home_weight_override_g: float | None = None
        self._home_shot_target_override_g: float | None = None
        self._home_warmup_override: bool | None = None
        self._home_steam_override: bool | None = None
        self._home_uptime_override_minutes: float | None = None
        self._home_temperature_c: float = DEFAULT_BREW_TARGET_TEMPERATURE_C
        self._home_pressure_bar: float = 0.2
        self._home_water_level_pct: float = DEFAULT_HOME_WATER_LEVEL_PCT
        self._home_weight_g: float = DEFAULT_HOME_WEIGHT_G
        self._home_shot_target_g: float = DEFAULT_HOME_SHOT_TARGET_G
        self._home_warmup_on: bool = True
        self._home_steam_on: bool = False
        self._home_uptime_minutes: float = 0.0
        self._home_last_uplink_preview: str = "No uplink packets received yet."
        self._last_control_event: str = "Idle"
        self._send_callback: Optional[Callable[[bytes], None]] = None
        self._stop_event = Event()
        self._wake_event = Event()
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
        self._home_started_monotonic = time.monotonic()
        self._home_pressure_bar = 0.2
        self._home_uptime_minutes = 0.0
        self._last_control_event = "Idle"
        self._stop_event.clear()
        self._wake_event.clear()
        self._thread = Thread(target=self._run, daemon=True, name="data_payload_dl")
        self._thread.start()

    def stop(self) -> None:
        """@brief Stop the downlink send thread and clear pending state."""
        self._stop_event.set()
        self._wake_event.set()
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
            self._home_pressure_bar = 0.2
            self._home_uptime_minutes = 0.0

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
        self._wake_event.set()

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
        self._wake_event.set()

    def _resolve_profile_locked(self, profile_id: int | None) -> dict[str, object]:
        """@brief Resolve a profile id to the closest known preset entry."""

        if not self._profile_presets:
            return {
                "id": DEFAULT_BREW_PROFILE_ID,
                "name": DEFAULT_BREW_PROFILE_NAME,
                "target_temperature_c": DEFAULT_BREW_TARGET_TEMPERATURE_C,
                "target_pressure_bar": DEFAULT_BREW_TARGET_PRESSURE_BAR,
                "target_flow_ml_sec": DEFAULT_BREW_TARGET_FLOW_ML_SEC,
                "preinfusion_seconds": 0.0,
                "shot_target_g": DEFAULT_HOME_SHOT_TARGET_G,
            }

        desired = self._selected_profile_id if profile_id is None else int(profile_id)
        for profile in self._profile_presets:
            if int(profile.get("id", 0)) == desired:
                return profile
        return self._profile_presets[0]

    def _apply_profile_locked(self, profile: dict[str, object]) -> None:
        """@brief Copy one profile preset into active brew/home fields."""

        self._selected_profile_id = int(profile.get("id", DEFAULT_BREW_PROFILE_ID))
        self._selected_profile_name = str(profile.get("name", DEFAULT_BREW_PROFILE_NAME))
        self._brew_target_pressure_bar = float(profile.get("target_pressure_bar", DEFAULT_BREW_TARGET_PRESSURE_BAR))
        self._brew_target_flow_ml_sec = float(profile.get("target_flow_ml_sec", DEFAULT_BREW_TARGET_FLOW_ML_SEC))
        self._brew_target_temperature_c = float(profile.get("target_temperature_c", DEFAULT_BREW_TARGET_TEMPERATURE_C))
        if self._home_shot_target_override_g is None:
            self._home_shot_target_g = float(profile.get("shot_target_g", DEFAULT_HOME_SHOT_TARGET_G))
        if self._home_temperature_override_c is None:
            self._home_temperature_c = self._brew_target_temperature_c

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
    ) -> None:
        """@brief Update screen-7 Home overrides pushed from simulator UI."""

        with self._lock:
            if profile_id is not None:
                self._apply_profile_locked(self._resolve_profile_locked(profile_id))
            if temperature_c is not None:
                value = max(0.0, min(float(temperature_c), 160.0))
                self._home_temperature_override_c = value
                self._home_temperature_c = value
                # Keep target + live temperature aligned with Screen-7 set input.
                self._brew_target_temperature_c = value
            if water_level_pct is not None:
                value = max(0.0, min(float(water_level_pct), 100.0))
                self._home_water_level_override_pct = value
                self._home_water_level_pct = value
            if weight_g is not None:
                value = max(0.0, min(float(weight_g), 200.0))
                self._home_weight_override_g = value
                self._home_weight_g = value
            if shot_target_g is not None:
                value = max(0.0, min(float(shot_target_g), 200.0))
                self._home_shot_target_override_g = value
                self._home_shot_target_g = value
            if warmup_on is not None:
                self._home_warmup_override = bool(warmup_on)
                self._home_warmup_on = bool(warmup_on)
            if steam_on is not None:
                self._home_steam_override = bool(steam_on)
                self._home_steam_on = bool(steam_on)
            self._last_control_event = "Screen7HomeDataSet"
        self._wake_event.set()

    def select_profile(self, profile_id: int | None = None, *, offline: bool | None = None) -> None:
        """@brief Select active brew profile metadata for the next StartBrew.

        @details The simulator currently exposes one profile. The payload still
        records profile metadata so the transport contract supports expansion.
        """

        del offline
        with self._lock:
            profile = self._resolve_profile_locked(profile_id)
            self._apply_profile_locked(profile)
            self._brew_time_sec = DEFAULT_BREW_TIME_SEC
            self._last_control_event = "ProfileSelection"
        self._wake_event.set()

    def start_brew(self, profile_id: int | None = None, packet_interval_ms: int | None = None) -> None:
        """@brief Start brew-mode downlink packet generation."""

        with self._lock:
            profile = self._resolve_profile_locked(profile_id)
            self._apply_profile_locked(profile)
            self._brew_time_sec = DEFAULT_BREW_TIME_SEC
            del packet_interval_ms
            self._packet_interval_ms = DEFAULT_BREW_PACKET_INTERVAL_MS
            # Brew stream must stay dynamic; clear static overrides that can freeze progress.
            self._home_weight_override_g = None
            self._home_water_level_override_pct = None
            self._home_warmup_override = None
            self._brew_active = True
            self._brew_started_monotonic = time.monotonic()
            self._brew_elapsed_sec = 0.0
            self._simulation_enabled = False
            self._last_control_event = "StartBrew"
        self._wake_event.set()

    def stop_brew(self) -> None:
        """@brief Stop brew-mode packet generation."""

        with self._lock:
            self._brew_active = False
            self._brew_started_monotonic = 0.0
            self._brew_elapsed_sec = 0.0
            self._last_control_event = "StopBrew"
        self._wake_event.set()

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
                wake_triggered = self._wake_event.wait(timeout=interval_s)
                if wake_triggered:
                    self._wake_event.clear()
                if self._stop_event.is_set():
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
        temp_start = max(20.0, target_temperature_c - 8.0)

        for sample_index in range(BREW_SAMPLES_PER_CHANNEL):
            t_sec = elapsed_sec + (sample_dt_sec * float(sample_index))
            progress = max(0.0, min(t_sec / safe_brew_time, 1.0))

            pressure_bar = max(0.0, target_pressure_bar * progress)
            flow_ml_sec = max(0.0, target_flow_ml_sec * progress)
            temperature_c = temp_start + ((target_temperature_c - temp_start) * progress)

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
        brew_complete_after_send = False
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
        home_started_monotonic = 0.0
        home_temperature_override_c: float | None = None
        home_water_level_override_pct: float | None = None
        home_weight_override_g: float | None = None
        home_shot_target_override_g: float | None = None
        home_warmup_override: bool | None = None
        home_steam_override: bool | None = None
        home_uptime_override_minutes: float | None = None

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
                    self._brew_elapsed_sec = brew_time_sec
                    brew_elapsed_sec = brew_time_sec
                    brew_complete_after_send = True
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

            home_started_monotonic = self._home_started_monotonic
            home_temperature_override_c = self._home_temperature_override_c
            home_water_level_override_pct = self._home_water_level_override_pct
            home_weight_override_g = self._home_weight_override_g
            home_shot_target_override_g = self._home_shot_target_override_g
            home_warmup_override = self._home_warmup_override
            home_steam_override = self._home_steam_override
            home_uptime_override_minutes = self._home_uptime_override_minutes

        pressure_values: list[float] = []
        flow_values: list[float] = []
        temperature_values: list[float] = []
        weight_values: list[float] = []
        sample_cursor = 0
        profile_samples: dict[str, list[float]] | None = None
        if brew_mode:
            profile_samples = self._profile_sample_library.get(int(brew_profile_id))
            if profile_samples:
                pressure_table = profile_samples.get("pressure_bar", [])
                flow_table = profile_samples.get("flow_ml_s", [])
                temperature_table = profile_samples.get("temperature_c", [])
                weight_table = profile_samples.get("weight_g", [])
                sample_count = min(len(pressure_table), len(flow_table), len(temperature_table), len(weight_table))
                if sample_count > 0:
                    if brew_elapsed_sec >= brew_time_sec:
                        sample_cursor = sample_count - 1
                    else:
                        sample_cursor = min(
                            int(max(0.0, brew_elapsed_sec / BREW_PROFILE_SAMPLE_DT_SEC)),
                            sample_count - 1,
                        )
                    for sample_index in range(BREW_SAMPLES_PER_CHANNEL):
                        history_index = sample_cursor - (BREW_SAMPLES_PER_CHANNEL - 1 - sample_index)
                        idx = max(0, min(history_index, sample_count - 1))
                        pressure_values.append(float(pressure_table[idx]))
                        flow_values.append(float(flow_table[idx]))
                        temperature_values.append(float(temperature_table[idx]))
                        weight_values.append(float(weight_table[idx]))
            if not pressure_values or not flow_values or not temperature_values or not weight_values:
                sample_dt_sec = packet_interval_s / float(BREW_SAMPLES_PER_CHANNEL)
                pressure_values, flow_values, temperature_values = self._build_brew_channels(
                    brew_elapsed_sec,
                    sample_dt_sec,
                    target_pressure_bar=brew_target_pressure_bar,
                    target_flow_ml_sec=brew_target_flow_ml_sec,
                    target_temperature_c=brew_target_temperature_c,
                    brew_time_sec=brew_time_sec,
                )
                safe_brew_time_sec = max(0.001, brew_time_sec)
                shot_target_estimate_g = max(0.0, brew_target_flow_ml_sec * brew_time_sec)
                for sample_index in range(BREW_SAMPLES_PER_CHANNEL):
                    t_sec = brew_elapsed_sec + (sample_dt_sec * float(sample_index))
                    progress = max(0.0, min(t_sec / safe_brew_time_sec, 1.0))
                    weight_values.append(float(max(0.0, shot_target_estimate_g * progress)))
                sample_cursor = max(0, len(pressure_values) - 1)

            floats = [0.0] * DATA_SIZE_FLOATS
            for sample_index in range(BREW_SAMPLES_PER_CHANNEL):
                floats[sample_index] = pressure_values[sample_index]
                floats[BREW_SAMPLES_PER_CHANNEL + sample_index] = flow_values[sample_index]
                floats[(2 * BREW_SAMPLES_PER_CHANNEL) + sample_index] = temperature_values[sample_index]
                floats[(3 * BREW_SAMPLES_PER_CHANNEL) + sample_index] = weight_values[sample_index]

            text = (
                f"brew=on;profile={brew_profile_id};name={brew_profile_name};"
                f"elapsed_s={brew_elapsed_sec:.2f};brew_time_s={brew_time_sec:.1f};"
                f"int_ms={packet_interval_ms};seq={seq};sample_cursor={sample_cursor}"
            )
        else:
            # Fill all float fields with a time-domain sine sampled over one packet.
            floats = [
                float(amplitude * math.sin(phase_rad + (phase_step * sample_index)))
                for sample_index in range(DATA_SIZE_FLOATS)
            ]
            text = (
                f"sim=on;amp={amplitude:.3f};freq={frequency_hz:.3f};"
                f"int_ms={packet_interval_ms};seq={seq}"
            )

        flow_for_mass = flow_values if flow_values else [brew_target_flow_ml_sec]
        avg_flow_ml_sec = max(0.0, sum(flow_for_mass) / max(1, len(flow_for_mass)))
        default_pressure_bar = (
            pressure_values[-1]
            if pressure_values
            else 0.2
        )
        default_temperature_c = (
            temperature_values[-1]
            if temperature_values
            else (brew_target_temperature_c - 1.0 + (0.15 * math.sin(phase_rad)))
        )
        default_shot_target_g = max(0.0, brew_target_flow_ml_sec * brew_time_sec)
        default_weight_g = max(0.0, avg_flow_ml_sec * brew_elapsed_sec) if brew_mode else 0.0
        if brew_mode and profile_samples is not None:
            weight_samples = profile_samples.get("weight_g", [])
            if weight_samples:
                idx = min(sample_cursor, len(weight_samples) - 1)
                default_weight_g = max(0.0, float(weight_samples[idx]))
                default_shot_target_g = max(default_shot_target_g, float(weight_samples[-1]))
        if brew_mode and brew_time_sec > 0.0:
            # Keep shot-progress percentage aligned to elapsed brew time.
            time_progress = max(0.0, min(brew_elapsed_sec / brew_time_sec, 1.0))
            default_weight_g = min(default_weight_g, default_shot_target_g * time_progress)
            if brew_elapsed_sec >= brew_time_sec:
                default_weight_g = default_shot_target_g
        default_water_level_pct = max(0.0, min(100.0, 100.0 - (default_weight_g * 0.45)))
        default_warmup_on = default_temperature_c < (brew_target_temperature_c - 0.6)
        default_steam_on = False
        default_uptime_minutes = max(0.0, (time.monotonic() - home_started_monotonic) / 60.0)

        home_temperature_c = (
            default_temperature_c
            if brew_mode
            else (
                float(home_temperature_override_c)
                if home_temperature_override_c is not None
                else default_temperature_c
            )
        )
        home_water_level_pct = (
            default_water_level_pct
            if brew_mode
            else (
                float(home_water_level_override_pct)
                if home_water_level_override_pct is not None
                else default_water_level_pct
            )
        )
        home_weight_g = (
            default_weight_g
            if brew_mode
            else (
                float(home_weight_override_g)
                if home_weight_override_g is not None
                else default_weight_g
            )
        )
        home_shot_target_g = (
            float(home_shot_target_override_g)
            if home_shot_target_override_g is not None
            else default_shot_target_g
        )
        home_warmup_on = (
            default_warmup_on
            if brew_mode
            else (bool(home_warmup_override) if home_warmup_override is not None else default_warmup_on)
        )
        home_steam_on = bool(home_steam_override) if home_steam_override is not None else default_steam_on
        home_uptime_minutes = (
            float(home_uptime_override_minutes)
            if home_uptime_override_minutes is not None
            else default_uptime_minutes
        )

        if not brew_mode:
            # Keep Brew/Home live temperature deterministic for client Home UI
            # when Screen-7 temperature is edited outside StartBrew mode.
            for sample_index in range(BREW_SAMPLES_PER_CHANNEL):
                temp_slot = (2 * BREW_SAMPLES_PER_CHANNEL) + sample_index
                if temp_slot >= DATA_SIZE_FLOATS:
                    break
                floats[temp_slot] = float(home_temperature_c)
            # Keep a deterministic weight channel in non-brew mode too.
            for sample_index in range(BREW_SAMPLES_PER_CHANNEL):
                weight_slot = (3 * BREW_SAMPLES_PER_CHANNEL) + sample_index
                if weight_slot >= DATA_SIZE_FLOATS:
                    break
                floats[weight_slot] = float(home_weight_g)

        brew_home_state = LCDControllerBrewHomeState(
            profile_id=int(brew_profile_id),
            brew_elapsed_ms=int(round(brew_elapsed_sec * 1000.0)),
            brew_duration_ms=int(round(brew_time_sec * 1000.0)),
            target_temperature_c=float(brew_target_temperature_c),
            target_pressure_bar=float(brew_target_pressure_bar),
            target_flow_ml_s=float(brew_target_flow_ml_sec),
            live_pressure_bar=float(default_pressure_bar),
            live_temperature_c=float(home_temperature_c),
            live_water_level_pct=float(home_water_level_pct),
            live_weight_g=float(home_weight_g),
            shot_target_preview_g=float(home_shot_target_g),
            warmup_on=bool(home_warmup_on),
            steam_on=bool(home_steam_on),
            uptime_minutes=0.0,
        )
        ints = brew_home_state_to_legacy_int_slots(brew_home_state)
        ints[0] = seq & 0x7FFFFFFF

        payload = encode_downlink(seq, floats, ints, text)
        try:
            cb(payload)
            with self._lock:
                self._dl_tx_count += 1
                if brew_mode:
                    self._brew_elapsed_sec = brew_elapsed_sec
                    if brew_complete_after_send and self._brew_active:
                        self._brew_active = False
                        self._brew_started_monotonic = 0.0
                        self._last_control_event = "BrewComplete"
                self._home_pressure_bar = default_pressure_bar
                self._home_temperature_c = home_temperature_c
                self._home_water_level_pct = home_water_level_pct
                self._home_weight_g = home_weight_g
                self._home_shot_target_g = home_shot_target_g
                self._home_warmup_on = home_warmup_on
                self._home_steam_on = home_steam_on
                self._home_uptime_minutes = home_uptime_minutes
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
            payload_text = str(pkt.get("s", "")).strip()
            if not payload_text:
                payload_text = "<empty>"
            self._home_last_uplink_preview = (
                f"seq={int(pkt.get('seq', 0))}, ts={int(pkt.get('timestamp_ms', 0))}, text={payload_text[:48]}"
            )

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
            uptime_minutes = (
                float(self._home_uptime_override_minutes)
                if self._home_uptime_override_minutes is not None
                else max(0.0, (time.monotonic() - self._home_started_monotonic) / 60.0)
            )
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
                "home_temperature_c": self._home_temperature_c,
                "home_pressure_bar": self._home_pressure_bar,
                "home_water_level_pct": self._home_water_level_pct,
                "home_weight_g": self._home_weight_g,
                "home_shot_target_g": self._home_shot_target_g,
                "home_warmup_on": self._home_warmup_on,
                "home_steam_on": self._home_steam_on,
                "home_uptime_minutes": uptime_minutes,
                "home_last_uplink_preview": self._home_last_uplink_preview,
                "profile_count": len(self._profile_presets),
                "profiles": [dict(profile) for profile in self._profile_presets],
            }


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

data_payload_manager = DataPayloadManager()
