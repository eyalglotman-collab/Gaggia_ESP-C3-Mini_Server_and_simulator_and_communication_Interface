"""Binary data payload channel for the Eyal Espresso simulator.

Server → Client (downlink):
    Sequential FIFO.  A background thread generates one packet every 20 ms
    and calls ``send_callback`` to deliver it over the active serial/TCP path.
    The sequence counter starts at zero on every ``start()`` call so the
    client can detect gaps.

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

DOWNLINK_INTERVAL_S: float = 0.02
DEFAULT_SINE_AMPLITUDE: float = 1.0
DEFAULT_SINE_FREQUENCY_HZ: float = 1.0


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
        self._sine_phase_rad: float = 0.0
        self._send_callback: Optional[Callable[[bytes], None]] = None
        self._stop_event = Event()
        self._thread: Optional[Thread] = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self, send_callback: Callable[[bytes], None]) -> None:
        """@brief Start the 20 ms downlink send thread.

        @details ``send_callback(payload_bytes)`` is called from the
        background thread every 20 ms when the session is active.  The
        caller (``link_state_machine``) should wrap the callback with the
        appropriate lock and state guard.

        @param send_callback Callable that sends one DATA frame payload.
        """
        self.stop()
        self._send_callback = send_callback
        self._dl_seq = 0
        self._simulation_enabled = False
        self._sine_phase_rad = 0.0
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

    # ------------------------------------------------------------------
    # Simulation control
    # ------------------------------------------------------------------

    def set_simulation_enabled(self, enabled: bool) -> None:
        """@brief Enable or disable sine-wave downlink generation."""
        with self._lock:
            self._simulation_enabled = bool(enabled)

    def configure_sine(self, *, amplitude: float | None = None, frequency_hz: float | None = None) -> None:
        """@brief Update sine generator amplitude and frequency parameters.

        @details Parameters are clamped to safe positive ranges so malformed
        command payloads cannot create invalid values in the generator.

        @param amplitude    Optional new sine amplitude.
        @param frequency_hz Optional new sine frequency in Hz.
        """
        with self._lock:
            if amplitude is not None:
                self._sine_amplitude = max(0.0, min(float(amplitude), 1000.0))
            if frequency_hz is not None:
                self._sine_frequency_hz = max(0.01, min(float(frequency_hz), 1000.0))

    # ------------------------------------------------------------------
    # Downlink send thread
    # ------------------------------------------------------------------

    def _run(self) -> None:
        """@brief Background 20 ms send loop."""
        try:
            while not self._stop_event.wait(timeout=DOWNLINK_INTERVAL_S):
                self._generate_and_send()
        except Exception as exc:
            print(f"\n[data-payload-dl] UNHANDLED CRASH: {exc}", flush=True)
            traceback.print_exc()

    def _generate_and_send(self) -> None:
        """@brief Build one sine-wave downlink packet and send it when enabled."""
        cb = self._send_callback
        if cb is None:
            return
        with self._lock:
            if not self._simulation_enabled:
                return
            seq = self._dl_seq
            self._dl_seq = (self._dl_seq + 1) & 0xFFFFFFFF
            amplitude = self._sine_amplitude
            frequency_hz = self._sine_frequency_hz
            phase_rad = self._sine_phase_rad
            sample_period_s = DOWNLINK_INTERVAL_S / float(DATA_SIZE_FLOATS)
            phase_step = 2.0 * math.pi * frequency_hz * sample_period_s
            self._sine_phase_rad = (
                phase_rad + (phase_step * float(DATA_SIZE_FLOATS))
            ) % (2.0 * math.pi)

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
        text = f"sim=on;amp={amplitude:.3f};freq={frequency_hz:.3f};seq={seq}"

        payload = encode_downlink(seq, floats, ints, text)
        try:
            cb(payload)
            with self._lock:
                self._dl_tx_count += 1
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
                ``sim_frequency_hz``.
        """
        with self._lock:
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
            }


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

data_payload_manager = DataPayloadManager()
