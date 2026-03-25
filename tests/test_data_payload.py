import math
import struct
import time

import pytest

from server.sim import data_payload


def _first_float_from_downlink(payload: bytes) -> float:
    fields = struct.unpack(data_payload._DL_FMT, payload)  # noqa: SLF001 - targeted transport-contract test
    return float(fields[2])


def _float_slice_from_downlink(payload: bytes, start: int, count: int) -> list[float]:
    fields = struct.unpack(data_payload._DL_FMT, payload)  # noqa: SLF001 - targeted transport-contract test
    float_values = list(fields[2 : 2 + data_payload.DATA_SIZE_FLOATS])
    return float_values[start : start + count]


def test_packet_interval_stats_and_throughput() -> None:
    manager = data_payload.DataPayloadManager()

    manager.configure_sine(packet_interval_ms=100)
    stats = manager.get_stats()
    assert stats["sim_packet_interval_ms"] == 100
    assert stats["sim_data_throughput_kbytes_per_sec"] == pytest.approx(4.0, rel=1e-9)

    manager.configure_sine(packet_interval_ms=5)
    assert manager.get_stats()["sim_packet_interval_ms"] == 10

    manager.configure_sine(packet_interval_ms=250)
    assert manager.get_stats()["sim_packet_interval_ms"] == 200


def test_sine_generation_phase_progression_uses_packet_interval() -> None:
    manager = data_payload.DataPayloadManager()
    captured_packets: list[bytes] = []

    manager._send_callback = captured_packets.append  # noqa: SLF001 - deterministic unit test hook
    manager.set_simulation_enabled(True)
    manager.configure_sine(amplitude=1.0, frequency_hz=1.0, packet_interval_ms=100)

    manager._generate_and_send(0.1)  # noqa: SLF001 - deterministic unit test hook
    manager._generate_and_send(0.1)  # noqa: SLF001 - deterministic unit test hook
    manager.configure_sine(packet_interval_ms=200)
    manager._generate_and_send(0.2)  # noqa: SLF001 - deterministic unit test hook

    assert len(captured_packets) == 3

    first_packet_sample = _first_float_from_downlink(captured_packets[0])
    second_packet_sample = _first_float_from_downlink(captured_packets[1])
    third_packet_sample = _first_float_from_downlink(captured_packets[2])

    assert first_packet_sample == pytest.approx(math.sin(2.0 * math.pi * 1.0 * 0.0), abs=1e-6)
    assert second_packet_sample == pytest.approx(math.sin(2.0 * math.pi * 1.0 * 0.1), abs=1e-6)
    assert third_packet_sample == pytest.approx(math.sin(2.0 * math.pi * 1.0 * 0.2), abs=1e-6)


def test_start_brew_generates_three_channels_of_ten_samples() -> None:
    manager = data_payload.DataPayloadManager()
    captured_packets: list[bytes] = []

    manager._send_callback = captured_packets.append  # noqa: SLF001 - deterministic unit test hook
    manager.start_brew(profile_id=1, packet_interval_ms=100)
    manager._generate_and_send(0.1)  # noqa: SLF001 - deterministic unit test hook

    assert len(captured_packets) == 1
    pressure_values = _float_slice_from_downlink(captured_packets[0], 0, 10)
    flow_values = _float_slice_from_downlink(captured_packets[0], 10, 10)
    temperature_values = _float_slice_from_downlink(captured_packets[0], 20, 10)

    assert len(pressure_values) == 10
    assert len(flow_values) == 10
    assert len(temperature_values) == 10
    assert max(pressure_values) >= 0.0
    assert max(flow_values) >= 0.0
    assert min(temperature_values) > 80.0


def test_brew_auto_stops_when_brew_time_is_reached() -> None:
    manager = data_payload.DataPayloadManager()
    manager._send_callback = lambda payload: None  # noqa: SLF001 - deterministic unit test hook
    manager.start_brew(profile_id=1, packet_interval_ms=100)

    with manager._lock:  # noqa: SLF001 - deterministic time warp
        manager._brew_started_monotonic = time.monotonic() - (manager._brew_time_sec + 0.1)

    manager._generate_and_send(0.1)  # noqa: SLF001 - deterministic unit test hook
    stats = manager.get_stats()
    assert stats["brew_active"] is False
    assert stats["last_control_event"] == "BrewComplete"
