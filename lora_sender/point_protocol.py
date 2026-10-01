"""58-byte ASCII trajectory protocol; one XYZ sample per frame."""
from collections import deque
from decimal import Decimal
import json
import math
import os
from pathlib import Path
import re
import tempfile
import time
import zlib

MAX_FRAME = 58
OVERHEAD = 27
MAX_PAYLOAD = MAX_FRAME - OVERHEAD
MAX_POINTS = 65531
ORIGIN_KEYS = ('latitude_deg', 'longitude_deg', 'altitude_m')
NUMBER = re.compile(r'-?\d+(?:\.\d+)?\Z')


def encode(sid, kind, seq, payload=b''):
    if not re.fullmatch(rb'[0-9A-F]{8}', sid) or kind not in (b'B', b'O', b'S', b'E', b'A', b'X'):
        raise ValueError('Invalid session or type')
    if not 0 <= seq <= 65535 or len(payload) > MAX_PAYLOAD:
        raise ValueError('Sequence or payload exceeds frame limit')
    if payload and not re.fullmatch(rb'[0-9A-Fa-z.,+-]+', payload):
        raise ValueError('Invalid payload characters')
    body = f'{len(payload):03X}'.encode() + kind + sid + f'{seq:04X}'.encode() + payload
    return b'\x01' + body + f'{zlib.crc32(body):08X}'.encode() + b'\r\n'


class Decoder:
    def __init__(self):
        self.buffer = bytearray()

    def feed(self, data):
        self.buffer.extend(data)
        packets = []
        while self.buffer:
            start = self.buffer.find(b'\x01')
            if start < 0:
                self.buffer.clear()
                break
            del self.buffer[:start]
            if len(self.buffer) < 4:
                break
            try:
                if not re.fullmatch(rb'[0-9A-F]{3}', self.buffer[1:4]):
                    raise ValueError()
                size = int(self.buffer[1:4], 16)
                if size > MAX_PAYLOAD:
                    raise ValueError()
                total = OVERHEAD + size
                # SOH cannot occur in an ASCII body: recover truncated frames.
                next_start = self.buffer.find(b'\x01', 1)
                if 0 < next_start < total:
                    del self.buffer[:next_start]
                    continue
                if len(self.buffer) < total:
                    break
                frame = bytes(self.buffer[:total])
                sid, kind = frame[5:13], frame[4:5]
                if not re.fullmatch(rb'[0-9A-F]{4}', frame[13:17]):
                    raise ValueError()
                seq = int(frame[13:17], 16)
                payload = frame[17:17 + size]
                if frame != encode(sid, kind, seq, payload):
                    raise ValueError()
                packets.append((sid, kind, seq, payload))
                del self.buffer[:total]
            except ValueError:
                del self.buffer[0]
        return packets


class Link:
    def __init__(self, port, rate=200, turn_delay=0.5):
        self.port, self.rate, self.turn_delay = port, rate, turn_delay
        self.decoder, self.pending = Decoder(), deque()
        self.received_bytes = self.received_frames = 0

    def send(self, *packet):
        frame = encode(*packet)
        time.sleep(self.turn_delay)
        if self.port.write(frame) != len(frame):
            raise OSError('Partial UART write')
        # Stop-and-wait ACK and pacing provide flow control without a drain ioctl.
        if self.rate:
            time.sleep(len(frame) / self.rate)

    def receive(self, timeout):
        deadline = time.monotonic() + timeout
        while True:
            if self.pending:
                return self.pending.popleft()
            if time.monotonic() >= deadline:
                return None
            data = self.port.read(min(max(self.port.in_waiting, 1), 512))
            self.received_bytes += len(data)
            packets = self.decoder.feed(data)
            self.received_frames += len(packets)
            self.pending.extend(packets)


def exchange(link, packet, timeout, retries):
    sid, _, seq, _ = packet
    for attempt in range(retries + 1):
        link.send(*packet)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            reply = link.receive(max(0, deadline - time.monotonic()))
            if reply is None:
                break
            if reply[0] == sid and reply[2] == seq:
                if reply[1] == b'A':
                    return
                if reply[1] == b'X':
                    raise RuntimeError('Receiver error: ' + reply[3].decode('ascii'))
        print(f'ACK timeout seq={seq}, attempt={attempt + 1}/{retries + 1}', flush=True)
    raise TimeoutError(f'No ACK for sequence {seq}')


def numeric(value):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError('Coordinates must be finite numbers')
    return value


def prepare(path, sid=None):
    """Validate every frame before opening the port or sending anything."""
    document = json.loads(Path(path).read_text())
    samples, origin = document['samples'], document['origin']
    if not isinstance(samples, list) or len(samples) > MAX_POINTS:
        raise ValueError(f'samples must be a list of at most {MAX_POINTS} points')
    sid = sid or os.urandom(4).hex().upper().encode()
    packets = [(sid, b'B', 0, str(len(samples)).encode())]
    checksum = 0
    for i, key in enumerate(ORIGIN_KEYS):
        value = numeric(origin[key])
        if key == 'latitude_deg' and not -90 <= value <= 90:
            raise ValueError('Invalid latitude')
        if key == 'longitude_deg' and not -180 <= value <= 180:
            raise ValueError('Invalid longitude')
        payload = f'{i},{format(Decimal(str(value)), "f")}'.encode()
        packets.append((sid, b'O', i + 1, payload))
    for i, row in enumerate(samples):
        values = [round(numeric(row[k]), 2) for k in ('x', 'y', 'z')]
        payload = ','.join(f'{0.0 if v == 0 else v:.2f}' for v in values).encode()
        packets.append((sid, b'S', i + 4, payload))
    for packet in packets[1:]:
        checksum = zlib.crc32(packet[1] + packet[3] + b'\n', checksum)
    packets.append((sid, b'E', len(samples) + 4, f'{len(samples)},{checksum:08X}'.encode()))
    for packet in packets:
        try:
            encode(*packet)
        except ValueError as error:
            raise ValueError(f'Sequence {packet[2]}: {error}') from error
    return packets


def send_points(link, packets, timeout=10, retries=5):
    total = len(packets) - 5
    print(f'TX session={packets[0][0].decode()}, points={total}, frames={len(packets)}, max_frame={max(len(encode(*p)) for p in packets)} B', flush=True)
    last = time.monotonic()
    for packet in packets:
        exchange(link, packet, timeout, retries)
        if packet[1] == b'B' or packet[1] == b'O':
            print(f'ACK type={packet[1].decode()} seq={packet[2]}', flush=True)
        if packet[1] == b'S' and time.monotonic() - last >= 5:
            print(f'Confirmed {packet[2] - 3}/{total} points', flush=True)
            last = time.monotonic()
    print(f'Complete: receiver saved trajectory_{packets[0][0].decode()}.json', flush=True)


class Receiver:
    def __init__(self, output_dir):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.sid = None
        self.last_packet = self.completed = None
        self.last_activity = time.monotonic()
        self.points = []

    def handle(self, packet):
        sid, kind, seq, payload = packet
        if kind not in (b'B', b'O', b'S', b'E'):
            return None
        if packet == self.completed or (self.sid == sid and packet == self.last_packet):
            self.last_activity = time.monotonic()
            return sid, b'A', seq, b''
        try:
            if kind == b'B' and seq == 0:
                if self.sid is not None:
                    raise ValueError('busy')
                if not payload.isdigit() or not 0 <= int(payload) <= MAX_POINTS:
                    raise ValueError('count')
                if (self.output_dir / f'trajectory_{sid.decode()}.json').exists():
                    raise ValueError('exists')
                self.sid, self.count = sid, int(payload)
                self.origin, self.points, self.checksum = {}, [], 0
                self.next_seq = 1
                print(f'RX session={sid.decode()} points={self.count}', flush=True)
            elif sid != self.sid:
                raise ValueError('session')
            elif seq != self.next_seq:
                raise ValueError('sequence')
            elif kind == b'O' and 1 <= seq <= 3:
                index, value = payload.decode().split(',')
                if index != str(seq - 1) or not NUMBER.fullmatch(value):
                    raise ValueError('origin')
                number = numeric(float(value))
                if seq == 1 and not -90 <= number <= 90 or seq == 2 and not -180 <= number <= 180:
                    raise ValueError('origin')
                self.origin[ORIGIN_KEYS[seq - 1]] = number
                self.checksum = zlib.crc32(kind + payload + b'\n', self.checksum)
                self.next_seq += 1
            elif kind == b'S' and seq >= 4:
                values = payload.decode().split(',')
                if len(self.origin) != 3 or len(self.points) >= self.count or len(values) != 3:
                    raise ValueError('point')
                if any(not re.fullmatch(r'-?\d+\.\d{2}', v) for v in values):
                    raise ValueError('point')
                self.points.append(dict(zip(('x', 'y', 'z'), [numeric(float(v)) for v in values])))
                self.checksum = zlib.crc32(kind + payload + b'\n', self.checksum)
                self.next_seq += 1
            elif kind == b'E':
                expected = f'{self.count},{self.checksum:08X}'.encode()
                if payload != expected or len(self.origin) != 3 or len(self.points) != self.count:
                    raise ValueError('checksum')
                self.save(sid)
                self.completed = packet
                self.sid = None
                self.points = []
            else:
                raise ValueError('type')
            self.last_activity, self.last_packet = time.monotonic(), packet
            return sid, b'A', seq, b''
        except (ValueError, KeyError, TypeError, OSError) as error:
            # Do not let an unrelated transfer cancel the active one.
            if sid == self.sid:
                self.sid, self.points = None, []
            print(f'RX error: {error}', flush=True)
            return sid, b'X', seq, b'rejected'

    def save(self, sid):
        dest = self.output_dir / f'trajectory_{sid.decode()}.json'
        origin = json.dumps(self.origin, indent=2).replace('\n', '\n  ')
        rows = [f'    {{"x": {p["x"]:.2f}, "y": {p["y"]:.2f}, "z": {p["z"]:.2f}}}' for p in self.points]
        text = '{\n  "origin": ' + origin + ',\n  "samples": [\n' + ',\n'.join(rows) + '\n  ]\n}\n'
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=self.output_dir, prefix='.point-', delete=False) as f:
                temporary = Path(f.name)
                f.write(text.encode())
                f.flush()
                os.fsync(f.fileno())
            os.link(temporary, dest)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        print(f'Saved {dest}: {len(self.points)} points, CRC32 verified', flush=True)

    def expire(self, timeout):
        if self.sid is not None and time.monotonic() - self.last_activity > timeout:
            print('Incomplete trajectory expired', flush=True)
            self.sid, self.points = None, []
