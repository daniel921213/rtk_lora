"""KCP message transport over a byte stream, with wire frames <= 240 bytes."""
import ctypes as C
from collections import deque
from pathlib import Path
import struct
import zlib

WIRE_MTU = 240
KCP_MTU = 234
MAX_MESSAGE = 127 * (KCP_MTU - 24)
_OUTPUT = C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_int, C.c_void_p, C.c_void_p)


def cobs_encode(data):
    out = bytearray([0])
    code_at, code = 0, 1
    for byte in data:
        if byte == 0:
            out[code_at] = code
            code_at = len(out)
            out.append(0)
            code = 1
        else:
            out.append(byte)
            code += 1
            if code == 255:
                out[code_at] = code
                code_at = len(out)
                out.append(0)
                code = 1
    out[code_at] = code
    return bytes(out)


def cobs_decode(data):
    out = bytearray()
    pos = 0
    while pos < len(data):
        code = data[pos]
        pos += 1
        end = pos + code - 1
        if code == 0 or end > len(data):
            raise ValueError('Invalid COBS frame')
        out.extend(data[pos:end])
        pos = end
        if code != 255 and pos < len(data):
            out.append(0)
    return bytes(out)


def encode_frame(packet):
    if not 24 <= len(packet) <= KCP_MTU:
        raise ValueError('Invalid KCP packet size')
    raw = packet + struct.pack('<I', zlib.crc32(packet))
    return cobs_encode(raw) + b'\x00'


class FrameDecoder:
    """Bounded parser; discard damaged frames and resynchronize at zero."""
    def __init__(self):
        self.buffer = bytearray()
        self.discard = False
        self.errors = 0

    def feed(self, data):
        packets = []
        for byte in data:
            if byte:
                if not self.discard:
                    self.buffer.append(byte)
                    if len(self.buffer) >= WIRE_MTU:
                        self.buffer.clear()
                        self.discard = True
                        self.errors += 1
                continue
            if not self.discard and self.buffer:
                try:
                    raw = cobs_decode(self.buffer)
                    if not 28 <= len(raw) <= KCP_MTU + 4:
                        raise ValueError('Invalid length')
                    packet, crc = raw[:-4], raw[-4:]
                    if struct.pack('<I', zlib.crc32(packet)) != crc:
                        raise ValueError('CRC mismatch')
                    packets.append(packet)
                except ValueError:
                    self.errors += 1
            self.buffer.clear()
            self.discard = False
        return packets


def _library():
    path = Path(__file__).with_name('liblorakcp.so')
    try:
        lib = C.CDLL(str(path))
    except OSError as exc:
        raise RuntimeError('Build KCP first: make -C lora/kcp') from exc
    signatures = {
        'ikcp_create': ([C.c_uint32, C.c_void_p], C.c_void_p),
        'ikcp_release': ([C.c_void_p], None),
        'ikcp_setoutput': ([C.c_void_p, _OUTPUT], None),
        'ikcp_send': ([C.c_void_p, C.c_char_p, C.c_int], C.c_int),
        'ikcp_input': ([C.c_void_p, C.c_char_p, C.c_long], C.c_int),
        'ikcp_update': ([C.c_void_p, C.c_uint32], None),
        'ikcp_peeksize': ([C.c_void_p], C.c_int),
        'ikcp_recv': ([C.c_void_p, C.c_void_p, C.c_int], C.c_int),
        'ikcp_waitsnd': ([C.c_void_p], C.c_int),
        'lora_kcp_config': ([C.c_void_p, C.c_int], None),
        'lora_kcp_failed': ([C.c_void_p], C.c_int),
    }
    for name, (args, result) in signatures.items():
        fn = getattr(lib, name)
        fn.argtypes, fn.restype = args, result
    return lib


class KcpLink:
    """Single-threaded endpoint. Drain outgoing, feed input, update every 50 ms.

    Both peers need the same fresh conversation ID. send() allows one pending
    message to bound memory; callers should retry once pending becomes zero.
    """
    def __init__(self, conv, rto_ms=1500):
        if not 0 <= conv <= 0xffffffff or not 100 <= rto_ms <= 60000:
            raise ValueError('conv must be uint32; rto_ms must be 100..60000')
        self.lib = _library()
        self.handle = self.lib.ikcp_create(conv, None)
        if not self.handle:
            raise MemoryError('ikcp_create failed')
        self.decoder = FrameDecoder()
        self.outgoing = deque()
        self.callback_error = None
        self._callback = _OUTPUT(self._output)
        self.lib.ikcp_setoutput(self.handle, self._callback)
        self.lib.lora_kcp_config(self.handle, rto_ms)

    def _output(self, data, size, _kcp, _user):
        try:
            if len(self.outgoing) >= 256:
                raise BufferError('Outgoing queue full; drain it before updating')
            self.outgoing.append(encode_frame(C.string_at(data, size)))
            return 0
        except Exception as exc:
            self.callback_error = exc
            return -1

    def _check(self):
        if not self.handle:
            raise RuntimeError('KcpLink is closed')
        if self.callback_error:
            raise self.callback_error

    @property
    def pending(self):
        self._check()
        return self.lib.ikcp_waitsnd(self.handle)

    def send(self, data):
        self._check()
        if len(data) > MAX_MESSAGE:
            raise ValueError(f'Message exceeds {MAX_MESSAGE} bytes')
        if self.pending:
            raise BufferError('Previous message is still awaiting ACK')
        result = self.lib.ikcp_send(self.handle, data, len(data))
        if result < 0:
            raise RuntimeError(f'ikcp_send failed: {result}')

    def feed(self, data):
        self._check()
        for packet in self.decoder.feed(data):
            if self.lib.ikcp_input(self.handle, packet, len(packet)) < 0:
                self.decoder.errors += 1

    def update(self, now_ms):
        self._check()
        self.lib.ikcp_update(self.handle, now_ms & 0xffffffff)
        self._check()
        if self.lib.lora_kcp_failed(self.handle):
            raise TimeoutError('KCP retry limit reached')

    def receive(self):
        self._check()
        messages = []
        while (size := self.lib.ikcp_peeksize(self.handle)) >= 0:
            buffer = C.create_string_buffer(max(size, 1))
            result = self.lib.ikcp_recv(self.handle, buffer, size)
            if result < 0:
                raise RuntimeError(f'ikcp_recv failed: {result}')
            messages.append(buffer.raw[:result])
        return messages

    def close(self):
        if self.handle:
            self.lib.ikcp_release(self.handle)
            self.handle = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
