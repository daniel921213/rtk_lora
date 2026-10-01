"""Serial and in-memory byte stream abstractions."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, Protocol

try:
    import serial  # type: ignore
except Exception:  # pragma: no cover - optional runtime dependency
    serial = None


class ByteStreamEndpoint(Protocol):
    """Simple byte stream endpoint used by sender/receiver."""

    def read(self, size: int = 1, timeout: float | None = None) -> bytes:
        ...

    def write(self, data: bytes) -> int:
        ...

    def close(self) -> None:
        ...


class SerialEndpoint:
    """PySerial adapter that matches ByteStreamEndpoint."""

    def __init__(self, port: str, baud: int, timeout: float = 0.1) -> None:
        if serial is None:  # pragma: no cover - depends on pyserial install
            raise RuntimeError("pyserial is required for SerialEndpoint")
        self._ser = serial.Serial(port=port, baudrate=baud, timeout=timeout)
        self._lock = threading.Lock()

    def read(self, size: int = 1, timeout: float | None = None) -> bytes:
        with self._lock:
            if timeout is None:
                return self._ser.read(size)
            old_timeout = self._ser.timeout
            self._ser.timeout = timeout
            try:
                return self._ser.read(size)
            finally:
                self._ser.timeout = old_timeout

    def write(self, data: bytes) -> int:
        with self._lock:
            return int(self._ser.write(data))

    def close(self) -> None:
        with self._lock:
            self._ser.close()


class InMemoryDuplexEndpoint:
    """Thread-safe in-memory duplex endpoint for tests/simulation."""

    def __init__(self) -> None:
        self._buffer = bytearray()
        self._cv = threading.Condition()
        self._peer: InMemoryDuplexEndpoint | None = None
        self._closed = False

    def connect_peer(self, peer: "InMemoryDuplexEndpoint") -> None:
        self._peer = peer

    def read(self, size: int = 1, timeout: float | None = None) -> bytes:
        deadline = None if timeout is None else time.monotonic() + timeout

        with self._cv:
            while not self._buffer and not self._closed:
                if deadline is None:
                    self._cv.wait()
                else:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        return b""
                    self._cv.wait(remaining)

            if not self._buffer:
                return b""

            n = min(max(size, 1), len(self._buffer))
            out = bytes(self._buffer[:n])
            del self._buffer[:n]
            return out

    def write(self, data: bytes) -> int:
        if self._closed:
            return 0
        if self._peer is None:
            raise RuntimeError("endpoint peer not connected")
        if not data:
            return 0

        with self._peer._cv:
            self._peer._buffer.extend(data)
            self._peer._cv.notify_all()
        return len(data)

    def close(self) -> None:
        with self._cv:
            self._closed = True
            self._cv.notify_all()



def create_memory_duplex() -> tuple[InMemoryDuplexEndpoint, InMemoryDuplexEndpoint]:
    """Create two connected in-memory duplex endpoints."""
    a = InMemoryDuplexEndpoint()
    b = InMemoryDuplexEndpoint()
    a.connect_peer(b)
    b.connect_peer(a)
    return a, b


@dataclass(slots=True)
class DelimitedFrameReader:
    """Read delimiter-terminated frames from a byte stream."""

    endpoint: ByteStreamEndpoint
    delimiter: int = 0x00
    read_chunk_size: int = 256
    _buffer: bytearray = field(default_factory=bytearray)

    def read_frame(self, timeout: float | None = None) -> bytes | None:
        """Read one frame excluding delimiter, or None on timeout."""
        deadline = None if timeout is None else time.monotonic() + timeout

        while True:
            delim_pos = self._buffer.find(bytes([self.delimiter]))
            if delim_pos >= 0:
                frame = bytes(self._buffer[:delim_pos])
                del self._buffer[: delim_pos + 1]
                return frame

            if deadline is None:
                chunk_timeout = 0.1
            else:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                chunk_timeout = min(0.1, remaining)

            chunk = self.endpoint.read(self.read_chunk_size, timeout=chunk_timeout)
            if not chunk:
                if deadline is not None and time.monotonic() >= deadline:
                    return None
                continue
            self._buffer.extend(chunk)



def iter_file_chunks(path: str | Path, chunk_size: int = 512) -> Iterator[bytes]:
    """Yield binary file chunks."""
    with Path(path).open("rb") as fp:
        while True:
            data = fp.read(chunk_size)
            if not data:
                break
            yield data


class BinaryFileSink:
    """Binary output sink for receiver output."""

    def __init__(self, path: str | Path, append: bool = True) -> None:
        mode = "ab" if append else "wb"
        self._fp = Path(path).open(mode)
        self._lock = threading.Lock()

    def write(self, data: bytes) -> int:
        with self._lock:
            self._fp.write(data)
            self._fp.flush()
        return len(data)

    def close(self) -> None:
        with self._lock:
            self._fp.close()



def open_serial_endpoint(path: str, baud: int, timeout: float = 0.1) -> SerialEndpoint:
    """Factory for a serial endpoint."""
    return SerialEndpoint(port=path, baud=baud, timeout=timeout)
