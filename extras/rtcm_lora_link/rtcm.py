"""RTCM3 frame parsing and validation."""

from __future__ import annotations

from dataclasses import dataclass, field

from crc import crc24q, verify_crc24q

RTCM3_PREAMBLE = 0xD3
RTCM3_MAX_PAYLOAD_LEN = 1023
RTCM3_HEADER_LEN = 3
RTCM3_CRC_LEN = 3


class RTCMParseError(ValueError):
    """Raised for invalid RTCM frame operations."""



def get_rtcm_payload_length(frame: bytes) -> int:
    """Extract payload length from a full RTCM3 frame."""
    if len(frame) < RTCM3_HEADER_LEN + RTCM3_CRC_LEN:
        raise RTCMParseError("frame too short")
    if frame[0] != RTCM3_PREAMBLE:
        raise RTCMParseError("invalid RTCM preamble")
    if frame[1] & 0xFC:
        raise RTCMParseError("reserved length bits are not zero")
    return ((frame[1] & 0x03) << 8) | frame[2]



def is_valid_rtcm_frame(frame: bytes) -> bool:
    """Return True if frame is a valid RTCM3 frame including CRC24Q."""
    try:
        payload_len = get_rtcm_payload_length(frame)
    except RTCMParseError:
        return False

    expected_len = RTCM3_HEADER_LEN + payload_len + RTCM3_CRC_LEN
    if len(frame) != expected_len:
        return False
    if payload_len > RTCM3_MAX_PAYLOAD_LEN:
        return False
    return verify_crc24q(frame)



def extract_rtcm_message_type(frame: bytes) -> int | None:
    """Extract 12-bit RTCM message type from a valid or structurally valid frame."""
    try:
        payload_len = get_rtcm_payload_length(frame)
    except RTCMParseError:
        return None

    if len(frame) < RTCM3_HEADER_LEN + payload_len + RTCM3_CRC_LEN:
        return None
    if payload_len < 2:
        return None

    payload = frame[RTCM3_HEADER_LEN : RTCM3_HEADER_LEN + payload_len]
    return ((payload[0] << 4) | (payload[1] >> 4)) & 0x0FFF



def build_rtcm_frame(payload: bytes) -> bytes:
    """Build a full RTCM3 frame from payload bytes and append CRC24Q."""
    payload_len = len(payload)
    if payload_len > RTCM3_MAX_PAYLOAD_LEN:
        raise RTCMParseError("payload too large for RTCM3")
    header = bytes([RTCM3_PREAMBLE, (payload_len >> 8) & 0x03, payload_len & 0xFF])
    body = header + payload
    crc = crc24q(body)
    return body + bytes([(crc >> 16) & 0xFF, (crc >> 8) & 0xFF, crc & 0xFF])


@dataclass
class RTCMStreamParser:
    """Incremental RTCM3 stream parser with resynchronization."""

    max_payload_len: int = RTCM3_MAX_PAYLOAD_LEN
    _buffer: bytearray = field(default_factory=bytearray)

    def feed(self, data: bytes) -> list[bytes]:
        """Feed stream bytes and return zero or more complete valid RTCM3 frames."""
        if not data:
            return []

        self._buffer.extend(data)
        frames: list[bytes] = []

        while True:
            preamble_pos = self._buffer.find(bytes([RTCM3_PREAMBLE]))
            if preamble_pos < 0:
                self._buffer.clear()
                break

            if preamble_pos > 0:
                del self._buffer[:preamble_pos]

            if len(self._buffer) < RTCM3_HEADER_LEN:
                break

            if self._buffer[1] & 0xFC:
                del self._buffer[0]
                continue

            payload_len = ((self._buffer[1] & 0x03) << 8) | self._buffer[2]
            if payload_len > self.max_payload_len:
                del self._buffer[0]
                continue

            total_len = RTCM3_HEADER_LEN + payload_len + RTCM3_CRC_LEN
            if len(self._buffer) < total_len:
                break

            candidate = bytes(self._buffer[:total_len])
            if is_valid_rtcm_frame(candidate):
                frames.append(candidate)
                del self._buffer[:total_len]
                continue

            del self._buffer[0]

        return frames

    def buffered_bytes(self) -> int:
        """Return current buffered byte count."""
        return len(self._buffer)

    def reset(self) -> None:
        """Clear internal parser buffer."""
        self._buffer.clear()
