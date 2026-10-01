"""Transport protocol frame definitions and codec."""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum

from crc import crc16_ccitt_false

PROTOCOL_VERSION = 1

FLAG_START = 0x01
FLAG_END = 0x02
FLAG_ACK_REQ = 0x04
FLAG_IS_ACK = 0x08
FLAG_IS_NACK = 0x10

MIN_FRAGMENT_WIRE_LEN = 9
MIN_ACK_WIRE_LEN = 9


class ProtocolError(Exception):
    """Base protocol exception."""


class ProtocolDecodeError(ProtocolError):
    """Raised when a wire frame cannot be decoded."""


class ProtocolValidationError(ProtocolError):
    """Raised when field validation fails."""


class ProtocolCRCError(ProtocolDecodeError):
    """Raised when a frame CRC check fails."""


class AckStatus(IntEnum):
    PARTIAL = 0
    COMPLETE = 1
    INVALID = 2
    ABORT = 3


@dataclass(slots=True)
class Fragment:
    """Transport fragment carrying a piece of one RTCM frame."""

    ver: int
    msg_id: int
    flags: int
    frag_idx: int
    frag_cnt: int
    payload: bytes

    def validate(self) -> None:
        if not (0 <= self.ver <= 0xFF):
            raise ProtocolValidationError("ver out of range")
        if not (0 <= self.msg_id <= 0xFFFF):
            raise ProtocolValidationError("msg_id out of range")
        if not (0 <= self.flags <= 0xFF):
            raise ProtocolValidationError("flags out of range")
        if self.flags & FLAG_IS_ACK:
            raise ProtocolValidationError("fragment cannot set IS_ACK")
        if not (1 <= self.frag_cnt <= 0xFF):
            raise ProtocolValidationError("frag_cnt must be 1..255")
        if not (0 <= self.frag_idx < self.frag_cnt):
            raise ProtocolValidationError("frag_idx must be < frag_cnt")
        if not (0 <= len(self.payload) <= 0xFF):
            raise ProtocolValidationError("payload length out of range")


@dataclass(slots=True)
class AckFrame:
    """Bitmap ACK frame for ACK-on-Error retransmission."""

    ver: int
    msg_id: int
    flags: int
    frag_cnt: int
    bitmap: bytes
    status: AckStatus

    def validate(self) -> None:
        if not (0 <= self.ver <= 0xFF):
            raise ProtocolValidationError("ver out of range")
        if not (0 <= self.msg_id <= 0xFFFF):
            raise ProtocolValidationError("msg_id out of range")
        if not (0 <= self.flags <= 0xFF):
            raise ProtocolValidationError("flags out of range")
        if not (self.flags & FLAG_IS_ACK):
            raise ProtocolValidationError("ack frame must set IS_ACK")
        if not (1 <= self.frag_cnt <= 0xFF):
            raise ProtocolValidationError("frag_cnt must be 1..255")
        expected_bitmap_len = (self.frag_cnt + 7) // 8
        if len(self.bitmap) != expected_bitmap_len:
            raise ProtocolValidationError(
                f"bitmap length mismatch: expected {expected_bitmap_len}, got {len(self.bitmap)}"
            )
        if not isinstance(self.status, AckStatus):
            raise ProtocolValidationError("status must be AckStatus")


def encode_fragment(fragment: Fragment) -> bytes:
    """Encode a Fragment to wire bytes (before COBS)."""
    fragment.validate()

    payload_len = len(fragment.payload)
    raw = bytearray()
    raw.append(fragment.ver & 0xFF)
    raw.extend([(fragment.msg_id >> 8) & 0xFF, fragment.msg_id & 0xFF])
    raw.append(fragment.flags & 0xFF)
    raw.append(fragment.frag_idx & 0xFF)
    raw.append(fragment.frag_cnt & 0xFF)
    raw.append(payload_len & 0xFF)
    raw.extend(fragment.payload)

    crc = crc16_ccitt_false(bytes(raw))
    raw.extend([(crc >> 8) & 0xFF, crc & 0xFF])
    return bytes(raw)


def decode_fragment(raw: bytes) -> Fragment:
    """Decode wire bytes into Fragment, verifying CRC16 and field validity."""
    if len(raw) < MIN_FRAGMENT_WIRE_LEN:
        raise ProtocolDecodeError("fragment raw frame too short")

    recv_crc = (raw[-2] << 8) | raw[-1]
    body = raw[:-2]
    calc_crc = crc16_ccitt_false(body)
    if recv_crc != calc_crc:
        raise ProtocolCRCError(f"fragment CRC16 mismatch recv=0x{recv_crc:04X} calc=0x{calc_crc:04X}")

    ver = body[0]
    msg_id = (body[1] << 8) | body[2]
    flags = body[3]
    frag_idx = body[4]
    frag_cnt = body[5]
    payload_len = body[6]

    payload = body[7:]
    if len(payload) != payload_len:
        raise ProtocolDecodeError(
            f"payload_len mismatch: header={payload_len} actual={len(payload)}"
        )

    fragment = Fragment(
        ver=ver,
        msg_id=msg_id,
        flags=flags,
        frag_idx=frag_idx,
        frag_cnt=frag_cnt,
        payload=payload,
    )
    fragment.validate()
    return fragment


def encode_ack(ack: AckFrame) -> bytes:
    """Encode an AckFrame to wire bytes (before COBS)."""
    ack.validate()

    raw = bytearray()
    raw.append(ack.ver & 0xFF)
    raw.extend([(ack.msg_id >> 8) & 0xFF, ack.msg_id & 0xFF])
    raw.append(ack.flags & 0xFF)
    raw.append(ack.frag_cnt & 0xFF)
    raw.append(len(ack.bitmap) & 0xFF)
    raw.extend(ack.bitmap)
    raw.append(int(ack.status) & 0xFF)

    crc = crc16_ccitt_false(bytes(raw))
    raw.extend([(crc >> 8) & 0xFF, crc & 0xFF])
    return bytes(raw)


def decode_ack(raw: bytes) -> AckFrame:
    """Decode wire bytes into AckFrame, verifying CRC16 and field validity."""
    if len(raw) < MIN_ACK_WIRE_LEN:
        raise ProtocolDecodeError("ack raw frame too short")

    recv_crc = (raw[-2] << 8) | raw[-1]
    body = raw[:-2]
    calc_crc = crc16_ccitt_false(body)
    if recv_crc != calc_crc:
        raise ProtocolCRCError(f"ack CRC16 mismatch recv=0x{recv_crc:04X} calc=0x{calc_crc:04X}")

    if len(body) < 7:
        raise ProtocolDecodeError("ack body too short")

    ver = body[0]
    msg_id = (body[1] << 8) | body[2]
    flags = body[3]
    frag_cnt = body[4]
    bitmap_len = body[5]

    if len(body) < 6 + bitmap_len + 1:
        raise ProtocolDecodeError("ack bitmap_len exceeds frame size")

    bitmap = body[6 : 6 + bitmap_len]
    status_raw = body[6 + bitmap_len]

    if len(body) != 6 + bitmap_len + 1:
        raise ProtocolDecodeError("ack frame has unexpected trailing bytes")

    try:
        status = AckStatus(status_raw)
    except ValueError as exc:
        raise ProtocolDecodeError(f"unknown ack status: {status_raw}") from exc

    ack = AckFrame(
        ver=ver,
        msg_id=msg_id,
        flags=flags,
        frag_cnt=frag_cnt,
        bitmap=bitmap,
        status=status,
    )
    ack.validate()
    return ack


def is_ack_wire_frame(raw: bytes) -> bool:
    """Quick check whether raw wire frame appears to be an ACK based on flags byte."""
    return len(raw) >= 4 and bool(raw[3] & FLAG_IS_ACK)
