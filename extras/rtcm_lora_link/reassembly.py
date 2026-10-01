"""Fragment reassembly manager for RTCM transport."""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from ack import bitmap_from_received, make_ack_frame
from protocol import AckFrame, AckStatus, Fragment
from rtcm import is_valid_rtcm_frame


class ReassemblyError(Exception):
    """Base reassembly exception."""


class ReassemblyValidationError(ReassemblyError):
    """Raised when fragment set is inconsistent."""


@dataclass(slots=True)
class ReassemblyEntry:
    """State of an in-flight message under one msg_id."""

    msg_id: int
    frag_cnt: int
    created_at: float = field(default_factory=time.monotonic)
    last_update: float = field(default_factory=time.monotonic)
    timeout_count: int = 0
    fragments: dict[int, bytes] = field(default_factory=dict)

    def add_fragment(self, fragment: Fragment) -> bool:
        """Insert fragment payload; return True if this index was newly added."""
        if fragment.msg_id != self.msg_id:
            raise ReassemblyValidationError("msg_id mismatch")
        if fragment.frag_cnt != self.frag_cnt:
            raise ReassemblyValidationError(
                f"frag_cnt mismatch current={self.frag_cnt} incoming={fragment.frag_cnt}"
            )
        if not (0 <= fragment.frag_idx < self.frag_cnt):
            raise ReassemblyValidationError("frag_idx out of range")

        self.last_update = time.monotonic()
        is_new = fragment.frag_idx not in self.fragments
        if is_new:
            self.fragments[fragment.frag_idx] = fragment.payload
        return is_new

    def received_indices(self) -> set[int]:
        """Return set of received fragment indices."""
        return set(self.fragments.keys())

    def bitmap(self) -> bytes:
        """Return bitmap bytes of received indices."""
        return bitmap_from_received(self.frag_cnt, self.fragments.keys())

    def is_complete(self) -> bool:
        """Return True when all fragments have been collected."""
        return len(self.fragments) == self.frag_cnt

    def reassemble(self) -> bytes:
        """Reassemble payload bytes in frag_idx order."""
        if not self.is_complete():
            raise ReassemblyValidationError("cannot reassemble incomplete entry")
        return b"".join(self.fragments[idx] for idx in range(self.frag_cnt))


@dataclass(slots=True)
class ReassemblyResult:
    """Output from processing one incoming fragment."""

    ack: AckFrame
    status: AckStatus
    completed_rtcm: bytes | None = None
    latency_s: float | None = None


@dataclass(slots=True)
class ReassemblyTimeoutRecord:
    """GC information for timed-out reassembly entries."""

    msg_id: int
    received_count: int
    expected_count: int
    bitmap: bytes
    timeout_count: int
    abort_ack: AckFrame


@dataclass(slots=True)
class ReassemblyMetrics:
    """Reassembly statistics."""

    reassembly_success: int = 0
    reassembly_fail: int = 0
    gc_timeouts: int = 0
    latencies_s: list[float] = field(default_factory=list)

    @property
    def avg_reassembly_latency(self) -> float:
        if not self.latencies_s:
            return 0.0
        return sum(self.latencies_s) / len(self.latencies_s)

    @property
    def max_reassembly_latency(self) -> float:
        if not self.latencies_s:
            return 0.0
        return max(self.latencies_s)


class ReassemblyManager:
    """Manage fragment collection per msg_id and validate final RTCM frame."""

    def __init__(self, timeout_s: float = 5.0, recent_ttl_s: float | None = None) -> None:
        self.timeout_s = timeout_s
        self.recent_ttl_s = recent_ttl_s if recent_ttl_s is not None else max(2.0, timeout_s * 2.0)
        self._entries: dict[int, ReassemblyEntry] = {}
        self._recent_complete: dict[int, tuple[float, int]] = {}
        self.metrics = ReassemblyMetrics()

    def _prune_recent(self, now: float) -> None:
        expired = [msg_id for msg_id, (expiry, _) in self._recent_complete.items() if now >= expiry]
        for msg_id in expired:
            self._recent_complete.pop(msg_id, None)

    def process_fragment(self, fragment: Fragment) -> ReassemblyResult:
        """Process one fragment and produce ACK + optional complete RTCM frame."""
        now = time.monotonic()
        self._prune_recent(now)

        recent = self._recent_complete.get(fragment.msg_id)
        if recent is not None:
            _, recent_frag_cnt = recent
            ack_frag_cnt = recent_frag_cnt if recent_frag_cnt > 0 else fragment.frag_cnt
            ack = make_ack_frame(
                msg_id=fragment.msg_id,
                frag_cnt=ack_frag_cnt,
                received_indices=range(ack_frag_cnt),
                status=AckStatus.COMPLETE,
            )
            return ReassemblyResult(ack=ack, status=AckStatus.COMPLETE, completed_rtcm=None)

        entry = self._entries.get(fragment.msg_id)
        if entry is None:
            entry = ReassemblyEntry(msg_id=fragment.msg_id, frag_cnt=fragment.frag_cnt)
            self._entries[fragment.msg_id] = entry

        if fragment.frag_cnt != entry.frag_cnt:
            ack = make_ack_frame(
                msg_id=fragment.msg_id,
                frag_cnt=entry.frag_cnt,
                received_indices=entry.received_indices(),
                status=AckStatus.INVALID,
            )
            self.metrics.reassembly_fail += 1
            return ReassemblyResult(ack=ack, status=AckStatus.INVALID)

        entry.add_fragment(fragment)

        if not entry.is_complete():
            ack = make_ack_frame(
                msg_id=fragment.msg_id,
                frag_cnt=entry.frag_cnt,
                received_indices=entry.received_indices(),
                status=AckStatus.PARTIAL,
            )
            return ReassemblyResult(ack=ack, status=AckStatus.PARTIAL)

        reassembled = entry.reassemble()
        latency = now - entry.created_at
        del self._entries[fragment.msg_id]

        if is_valid_rtcm_frame(reassembled):
            self.metrics.reassembly_success += 1
            self.metrics.latencies_s.append(latency)
            self._recent_complete[fragment.msg_id] = (time.monotonic() + self.recent_ttl_s, fragment.frag_cnt)
            ack = make_ack_frame(
                msg_id=fragment.msg_id,
                frag_cnt=fragment.frag_cnt,
                received_indices=range(fragment.frag_cnt),
                status=AckStatus.COMPLETE,
            )
            return ReassemblyResult(
                ack=ack,
                status=AckStatus.COMPLETE,
                completed_rtcm=reassembled,
                latency_s=latency,
            )

        self.metrics.reassembly_fail += 1
        ack = make_ack_frame(
            msg_id=fragment.msg_id,
            frag_cnt=fragment.frag_cnt,
            received_indices=range(fragment.frag_cnt),
            status=AckStatus.INVALID,
        )
        return ReassemblyResult(ack=ack, status=AckStatus.INVALID)

    def collect_garbage(self) -> list[ReassemblyTimeoutRecord]:
        """Expire stale entries and return timeout records for logging/abort ACK."""
        now = time.monotonic()
        self._prune_recent(now)
        expired_ids = [
            msg_id
            for msg_id, entry in self._entries.items()
            if (now - entry.last_update) >= self.timeout_s
        ]

        records: list[ReassemblyTimeoutRecord] = []
        for msg_id in expired_ids:
            entry = self._entries.pop(msg_id)
            entry.timeout_count += 1
            self.metrics.gc_timeouts += 1
            self.metrics.reassembly_fail += 1
            abort_ack = make_ack_frame(
                msg_id=msg_id,
                frag_cnt=entry.frag_cnt,
                received_indices=entry.received_indices(),
                status=AckStatus.ABORT,
            )
            records.append(
                ReassemblyTimeoutRecord(
                    msg_id=msg_id,
                    received_count=len(entry.fragments),
                    expected_count=entry.frag_cnt,
                    bitmap=entry.bitmap(),
                    timeout_count=entry.timeout_count,
                    abort_ack=abort_ack,
                )
            )
        return records

    def snapshot_bitmap(self, msg_id: int) -> bytes | None:
        """Return current bitmap for msg_id if present."""
        entry = self._entries.get(msg_id)
        if entry is None:
            return None
        return entry.bitmap()
