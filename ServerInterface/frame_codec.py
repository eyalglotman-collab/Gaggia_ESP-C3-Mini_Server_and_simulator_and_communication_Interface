"""Portable transport frame codec shared by the server interface layer."""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum

SOF = b"\xA5\x5A"
HEADER_SIZE = 2 + 1 + 2 + 4 + 4 + 2
CRC_SIZE = 2


class MessageType(IntEnum):
    """@brief Enumerate the low-level transport packet types.

    @details This Python reference mirrors the portable native ServerInterface
    definitions so the server can exercise the same protocol rules that are
    intended for later STM32 reuse.
    """

    RESET = 1
    INITIALIZE = 2
    CONNECT = 3
    DISCONNECT = 4
    KEEPALIVE = 5
    ERROR = 6
    ACK = 7
    DATA = 8


@dataclass(slots=True)
class Frame:
    """@brief Describe one framed transport packet.

    @details The frame carries counters, a sequence number, payload bytes, and
    is encoded with the same binary layout as the native ServerInterface core.
    """

    message_type: MessageType
    host_live_integer: int
    device_live_integer: int
    sequence: int
    payload: bytes = b""


class FrameDecodeError(ValueError):
    """@brief Raised when a frame cannot be decoded safely."""


def crc16_ccitt(data: bytes) -> int:
    """@brief Compute CRC16-CCITT over the provided bytes.

    @details The logic intentionally matches the native ServerInterface
    implementation so regression tests can validate one canonical algorithm.
    """

    crc = 0xFFFF
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return crc


def encode_frame(frame: Frame) -> bytes:
    """@brief Encode a frame into transport-ready bytes.

    @details The encoded layout is SOF, type, payload length, host live
    integer, device live integer, sequence, payload, and CRC16.
    """

    payload = frame.payload or b""
    header = (
        SOF
        + bytes([int(frame.message_type)])
        + len(payload).to_bytes(2, "little")
        + int(frame.host_live_integer).to_bytes(4, "little", signed=False)
        + int(frame.device_live_integer).to_bytes(4, "little", signed=False)
        + int(frame.sequence).to_bytes(2, "little", signed=False)
    )
    body = header + payload
    crc = crc16_ccitt(body).to_bytes(2, "little")
    return body + crc


def decode_frames(buffer: bytearray) -> list[Frame]:
    """@brief Decode complete frames from a mutable buffer.

    @details Incomplete trailing bytes remain in the buffer so the caller can
    append more transport data later without losing framing state.
    """

    frames: list[Frame] = []

    while True:
        sof_index = buffer.find(SOF)
        if sof_index < 0:
            buffer.clear()
            break
        if sof_index > 0:
            del buffer[:sof_index]

        if len(buffer) < HEADER_SIZE + CRC_SIZE:
            break

        payload_length = int.from_bytes(buffer[3:5], "little")
        total_length = HEADER_SIZE + payload_length + CRC_SIZE
        if len(buffer) < total_length:
            break

        packet = bytes(buffer[:total_length])
        expected_crc = int.from_bytes(packet[-2:], "little")
        actual_crc = crc16_ccitt(packet[:-2])
        if expected_crc != actual_crc:
            del buffer[:2]
            raise FrameDecodeError(
                f"CRC mismatch: expected 0x{expected_crc:04X}, computed 0x{actual_crc:04X}."
            )

        try:
            message_type = MessageType(packet[2])
        except ValueError as exc:
            del buffer[:total_length]
            raise FrameDecodeError(f"Unsupported message type {packet[2]}") from exc

        frames.append(
            Frame(
                message_type=message_type,
                host_live_integer=int.from_bytes(packet[5:9], "little"),
                device_live_integer=int.from_bytes(packet[9:13], "little"),
                sequence=int.from_bytes(packet[13:15], "little"),
                payload=packet[15:-2],
            )
        )
        del buffer[:total_length]

    return frames

