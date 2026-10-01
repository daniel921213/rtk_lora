"""Binary-safe RYLR AT framing and CRC-24Q validated RTCM3 streams.

No extra on-air headers: split RTCM bytes into <=240 byte radio payloads.
A local +OK is NOT an acknowledgement from the destination.
"""
import re
import time

MAX_PAYLOAD = 240


def crc24q(data):
    crc = 0
    for value in data:
        crc ^= value << 16
        for _ in range(8):
            crc <<= 1
            if crc & 0x1000000:
                crc ^= 0x1864CFB
    return crc & 0xFFFFFF


def send_command(address, payload):
    if not 0 <= address <= 65535:
        raise ValueError('address must be 0..65535')
    if not 1 <= len(payload) <= MAX_PAYLOAD:
        raise ValueError('payload must contain 1..240 bytes')
    return f'AT+SEND={address},{len(payload)},'.encode('ascii') + payload + b'\r\n'


class RYLRParser:
    """Incremental parser; payload is bytes and may contain CR/LF, NUL or commas.

    Events: ('rcv', address, payload, rssi, snr), ('line', bytes),
    ('malformed', bytes). An incomplete record expires after timeout seconds.
    """
    header = re.compile(rb'^\+RCV=(\d{1,5}),(\d{1,3}),')
    trailer = re.compile(rb'^,(-?\d{1,4}),(-?\d{1,4})\r\n')

    def __init__(self, timeout=2.0):
        self.buffer = bytearray()
        self.timeout = timeout
        self.last_data = 0

    def feed(self, data, now=None):
        now = time.monotonic() if now is None else now
        events = []
        if self.buffer and now - self.last_data > self.timeout:
            events.append(('malformed', bytes(self.buffer)))
            self.buffer.clear()
        if data:
            self.last_data = now
            self.buffer.extend(data)
        while self.buffer:
            if self.buffer.startswith(b'+RCV='):
                match = self.header.match(self.buffer)
                if match is None:
                    if len(self.buffer) < 18 and b'\r\n' not in self.buffer:
                        break
                    events.append(('malformed', bytes(self.buffer[:1])))
                    del self.buffer[:1]
                    continue
                address, size = map(int, match.groups())
                if address > 65535 or not 1 <= size <= MAX_PAYLOAD:
                    events.append(('malformed', bytes(self.buffer[:match.end()])))
                    del self.buffer[:match.end()]
                    continue
                end = match.end() + size
                if len(self.buffer) < end:
                    break
                tail = self.buffer[end:]
                trailer = self.trailer.match(tail)
                if trailer is None:
                    if len(tail) < 16 and b'\r\n' not in tail:
                        break
                    events.append(('malformed', bytes(self.buffer[:end])))
                    del self.buffer[:end]
                    continue
                events.append(('rcv', address, bytes(self.buffer[match.end():end]),
                               int(trailer[1]), int(trailer[2])))
                del self.buffer[:end + trailer.end()]
            else:
                end = self.buffer.find(b'\r\n')
                # Resynchronise after noise without consuming a following RCV payload.
                start = self.buffer.find(b'+RCV=')
                if start > 0 and (end < 0 or start < end):
                    events.append(('line', bytes(self.buffer[:start])))
                    del self.buffer[:start]
                elif end >= 0:
                    if end:
                        events.append(('line', bytes(self.buffer[:end])))
                    del self.buffer[:end + 2]
                elif len(self.buffer) > 4096:
                    events.append(('malformed', bytes(self.buffer[:-5])))
                    del self.buffer[:-5]
                else:
                    break
        return events


class RTCMParser:
    """Recover whole RTCM3 messages, reject corrupted/lost-fragment messages."""
    def __init__(self, timeout=3.0):
        self.buffer = bytearray()
        self.timeout = timeout
        self.last_data = 0
        self.crc_errors = 0
        self.discarded_bytes = 0

    def feed(self, data, now=None):
        now = time.monotonic() if now is None else now
        if self.buffer and now - self.last_data > self.timeout:
            self.discarded_bytes += len(self.buffer)
            self.buffer.clear()
        if data:
            self.buffer.extend(data)
            self.last_data = now
        frames = []
        while self.buffer:
            start = self.buffer.find(b'\xd3')
            if start < 0:
                self.discarded_bytes += len(self.buffer)
                self.buffer.clear()
                break
            if start:
                self.discarded_bytes += start
                del self.buffer[:start]
            if len(self.buffer) < 3:
                break
            if self.buffer[1] & 0xFC:
                self.discarded_bytes += 1
                del self.buffer[0]
                continue
            length = ((self.buffer[1] & 3) << 8) | self.buffer[2]
            size = length + 6
            if len(self.buffer) < size:
                break
            frame = bytes(self.buffer[:size])
            if crc24q(frame[:-3]) == int.from_bytes(frame[-3:], 'big'):
                frames.append(frame)
                del self.buffer[:size]
            else:
                self.crc_errors += 1
                self.discarded_bytes += 1
                del self.buffer[0]
        return frames


def write_all(stream, data):
    view = memoryview(data)
    while view:
        size = stream.write(view)
        if not size:
            raise OSError('serial write made no progress')
        view = view[size:]


def wait_ok(port, parser, timeout):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for event in parser.feed(port.read(port.in_waiting or 1)):
            if event[0] == 'line':
                if event[1] == b'+OK':
                    return
                if event[1].startswith(b'+ERR'):
                    raise RuntimeError('RYLR rejected command: ' + repr(event[1]))
    raise TimeoutError('RYLR +OK timeout; stopping to avoid overlapping AT commands')
