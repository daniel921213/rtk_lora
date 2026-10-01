"""Minimal COBS codec implementation for serial framing."""

from __future__ import annotations


class COBSDecodeError(ValueError):
    """Raised when COBS decoding fails due to malformed input."""


def cobs_encode(data: bytes) -> bytes:
    """Encode bytes using Consistent Overhead Byte Stuffing (COBS)."""
    if not data:
        return b"\x01"

    out = bytearray()
    code_index = 0
    out.append(0)  # placeholder for first code byte
    code = 1

    for byte in data:
        if byte == 0:
            out[code_index] = code
            code_index = len(out)
            out.append(0)
            code = 1
        else:
            out.append(byte)
            code += 1
            if code == 0xFF:
                out[code_index] = code
                code_index = len(out)
                out.append(0)
                code = 1

    out[code_index] = code
    return bytes(out)


def cobs_decode(data: bytes) -> bytes:
    """Decode COBS encoded bytes (without delimiter)."""
    if not data:
        raise COBSDecodeError("empty COBS frame")

    out = bytearray()
    i = 0
    n = len(data)

    while i < n:
        code = data[i]
        if code == 0:
            raise COBSDecodeError("zero byte in encoded frame")

        i += 1
        end = i + code - 1
        if end > n:
            raise COBSDecodeError("code byte exceeds remaining data")

        out.extend(data[i:end])
        i = end

        if code != 0xFF and i < n:
            out.append(0)

    return bytes(out)
