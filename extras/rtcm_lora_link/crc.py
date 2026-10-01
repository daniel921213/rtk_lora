"""CRC utilities for RTCM transport and RTCM3 CRC24Q."""

from __future__ import annotations

CRC24Q_POLY = 0x1864CFB
CRC24Q_INIT = 0x000000

CRC16_CCITT_FALSE_POLY = 0x1021
CRC16_CCITT_FALSE_INIT = 0xFFFF


class CRCError(ValueError):
    """Raised when a CRC check fails."""


def crc16_ccitt_false(data: bytes, init: int = CRC16_CCITT_FALSE_INIT) -> int:
    """Compute CRC16/CCITT-FALSE over data."""
    crc = init & 0xFFFF
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ CRC16_CCITT_FALSE_POLY) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return crc & 0xFFFF


def crc24q(data: bytes, init: int = CRC24Q_INIT) -> int:
    """Compute CRC24Q used by RTCM3."""
    crc = init & 0xFFFFFF
    for byte in data:
        crc ^= byte << 16
        for _ in range(8):
            if crc & 0x800000:
                crc = ((crc << 1) ^ CRC24Q_POLY) & 0xFFFFFF
            else:
                crc = (crc << 1) & 0xFFFFFF
    return crc & 0xFFFFFF


def append_crc16(data: bytes) -> bytes:
    """Return data + CRC16(CCITT-FALSE), big-endian."""
    crc = crc16_ccitt_false(data)
    return data + bytes([(crc >> 8) & 0xFF, crc & 0xFF])


def verify_crc16(data_with_crc: bytes) -> bool:
    """Verify trailing CRC16(CCITT-FALSE)."""
    if len(data_with_crc) < 2:
        return False
    body = data_with_crc[:-2]
    recv = (data_with_crc[-2] << 8) | data_with_crc[-1]
    calc = crc16_ccitt_false(body)
    return recv == calc


def append_crc24q(data: bytes) -> bytes:
    """Return data + CRC24Q, big-endian 3 bytes."""
    crc = crc24q(data)
    return data + bytes([(crc >> 16) & 0xFF, (crc >> 8) & 0xFF, crc & 0xFF])


def verify_crc24q(data_with_crc: bytes) -> bool:
    """Verify trailing CRC24Q."""
    if len(data_with_crc) < 3:
        return False
    body = data_with_crc[:-3]
    recv = (data_with_crc[-3] << 16) | (data_with_crc[-2] << 8) | data_with_crc[-1]
    calc = crc24q(body)
    return recv == calc
