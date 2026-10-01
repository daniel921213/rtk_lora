"""ACK bitmap helpers for ACK-on-Error retransmission."""

from __future__ import annotations

from typing import Iterable

from protocol import AckFrame, AckStatus, FLAG_IS_ACK, PROTOCOL_VERSION


class AckBitmapError(ValueError):
    """Raised for invalid bitmap operations."""



def bitmap_num_bytes(frag_cnt: int) -> int:
    """Return required bitmap bytes for frag_cnt bits."""
    if frag_cnt <= 0:
        raise AckBitmapError("frag_cnt must be > 0")
    return (frag_cnt + 7) // 8



def set_bitmap_bit(bitmap: bytearray, frag_idx: int) -> None:
    """Set bit for frag_idx in-place."""
    if frag_idx < 0:
        raise AckBitmapError("frag_idx must be >= 0")
    byte_idx = frag_idx // 8
    bit_idx = frag_idx % 8
    if byte_idx >= len(bitmap):
        raise AckBitmapError("frag_idx exceeds bitmap length")
    bitmap[byte_idx] |= 1 << bit_idx



def is_bitmap_bit_set(bitmap: bytes, frag_idx: int) -> bool:
    """Check whether frag_idx bit is set in bitmap."""
    if frag_idx < 0:
        return False
    byte_idx = frag_idx // 8
    bit_idx = frag_idx % 8
    if byte_idx >= len(bitmap):
        return False
    return bool(bitmap[byte_idx] & (1 << bit_idx))



def bitmap_from_received(frag_cnt: int, received_indices: Iterable[int]) -> bytes:
    """Build ACK bitmap from received fragment indices."""
    bitmap = bytearray(bitmap_num_bytes(frag_cnt))
    for idx in received_indices:
        if 0 <= idx < frag_cnt:
            set_bitmap_bit(bitmap, idx)
    return bytes(bitmap)



def received_indices_from_bitmap(bitmap: bytes, frag_cnt: int) -> set[int]:
    """Decode ACK bitmap into set of received indices."""
    out: set[int] = set()
    for idx in range(frag_cnt):
        if is_bitmap_bit_set(bitmap, idx):
            out.add(idx)
    return out



def missing_indices_from_bitmap(
    bitmap: bytes,
    frag_cnt: int,
    start_idx: int = 0,
    end_idx: int | None = None,
) -> list[int]:
    """Return missing fragment indices in [start_idx, end_idx)."""
    if end_idx is None:
        end_idx = frag_cnt
    start = max(0, start_idx)
    end = min(frag_cnt, end_idx)
    return [idx for idx in range(start, end) if not is_bitmap_bit_set(bitmap, idx)]



def all_fragments_received(bitmap: bytes, frag_cnt: int) -> bool:
    """Return True if every fragment bit is present."""
    return len(missing_indices_from_bitmap(bitmap, frag_cnt)) == 0



def make_ack_frame(
    msg_id: int,
    frag_cnt: int,
    received_indices: Iterable[int],
    status: AckStatus,
    *,
    version: int = PROTOCOL_VERSION,
) -> AckFrame:
    """Create a validated AckFrame from received fragment indices."""
    bitmap = bitmap_from_received(frag_cnt, received_indices)
    ack = AckFrame(
        ver=version,
        msg_id=msg_id,
        flags=FLAG_IS_ACK,
        frag_cnt=frag_cnt,
        bitmap=bitmap,
        status=status,
    )
    ack.validate()
    return ack
