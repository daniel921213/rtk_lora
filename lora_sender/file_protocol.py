"""Reliable stop-and-wait framing for a transparent, bidirectional serial link."""
import base64
import hashlib
import os
from pathlib import Path
import struct
import tempfile
import time
import zlib

HEADER = struct.Struct('!4s8scI')
MAGIC = b'LF02'
METADATA = struct.Struct('!IIB32s')
MAX_LINE = 4096
MAX_SIZE = 64 * 1024 * 1024


def encode(session, kind, seq, payload=b''):
    body = HEADER.pack(MAGIC, session, kind, seq) + payload
    return b'\n' + base64.b64encode(body + struct.pack('!I', zlib.crc32(body))) + b'\n'


def encode_metadata(meta):
    return METADATA.pack(meta['size'], meta['wire_size'],
                         1 if meta['codec'] == 'zlib' else 0,
                         bytes.fromhex(meta['sha256'])) + meta['name'].encode('utf-8')


def decode_metadata(payload):
    if len(payload) < METADATA.size:
        raise ValueError('Truncated metadata')
    size, wire_size, codec, digest = METADATA.unpack(payload[:METADATA.size])
    if codec not in (0, 1):
        raise ValueError('Unsupported compression')
    return {'name': payload[METADATA.size:].decode('utf-8'), 'size': size,
            'wire_size': wire_size, 'codec': 'zlib' if codec else 'none',
            'sha256': digest.hex()}


class Link:
    def __init__(self, port, rate=200, turn_delay=0.2, write_size=200):
        self.port, self.rate, self.turn_delay = port, rate, turn_delay
        self.write_size = write_size
        self.buffer = bytearray()
        self.received_bytes = 0
        self.received_frames = 0

    def send(self, session, kind, seq, payload=b''):
        frame = encode(session, kind, seq, payload)
        time.sleep(self.turn_delay)
        for offset in range(0, len(frame), self.write_size):
            part = frame[offset:offset + self.write_size]
            if self.port.write(part) != len(part):
                raise OSError('Incomplete serial write')
            self.port.flush()
            if self.rate:
                time.sleep(len(part) / self.rate)
            if offset + len(part) < len(frame):
                time.sleep(2)  # Allow radio to drain before a multi-write frame continues.

    def receive(self, timeout):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            data = self.port.read(1)
            if not data:
                continue
            self.received_bytes += len(data)
            if data != b'\n':
                self.buffer.extend(data)
                if len(self.buffer) > MAX_LINE:
                    self.buffer.clear()
                continue
            line = bytes(self.buffer)
            self.buffer.clear()
            try:
                packet = base64.b64decode(line, validate=True)
                if len(packet) < HEADER.size + 4:
                    continue
                body, checksum = packet[:-4], packet[-4:]
                if zlib.crc32(body) != struct.unpack('!I', checksum)[0]:
                    continue
                magic, session, kind, seq = HEADER.unpack(body[:HEADER.size])
                if magic == MAGIC:
                    self.received_frames += 1
                    return session, kind, seq, body[HEADER.size:]
            except (ValueError, struct.error):
                continue
        return None


def exchange(link, session, kind, seq, payload, timeout, retries):
    for attempt in range(retries + 1):
        link.send(session, kind, seq, payload)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            reply = link.receive(max(0, deadline - time.monotonic()))
            if reply is None:
                break
            sid, reply_kind, reply_seq, data = reply
            if sid != session or reply_seq != seq:
                continue
            if reply_kind == b'A':
                return
            if reply_kind == b'E':
                raise RuntimeError('Receiver: ' + data.decode('utf-8', errors='replace'))
        print(f'ACK timeout: sequence={seq}, attempt={attempt + 1}/{retries + 1}', flush=True)
    raise TimeoutError(f'No ACK for sequence {seq}; check receiver and radio settings')


def send_file(link, path, chunk_size=120, timeout=20, retries=5):
    path = Path(path)
    if path.stat().st_size > MAX_SIZE:
        raise ValueError(f'File exceeds {MAX_SIZE} bytes')
    raw = path.read_bytes()
    if len(raw) > MAX_SIZE:
        raise ValueError('File grew beyond size limit')
    packed = zlib.compress(raw, 9)
    # Some incompressible files get larger; send those without compression.
    wire, codec = (packed, 'zlib') if len(packed) < len(raw) else (raw, 'none')
    session = os.urandom(8)
    meta = {'name': path.name, 'size': len(raw), 'wire_size': len(wire),
            'sha256': hashlib.sha256(raw).hexdigest(), 'codec': codec}
    metadata = encode_metadata(meta)
    if len(encode(session, b'B', 0, metadata)) > MAX_LINE:
        raise ValueError('Filename is too long')
    print(f'TX {path.name}: original={len(raw)} B, transmitted payload={len(wire)} B ({codec})', flush=True)
    exchange(link, session, b'B', 0, metadata, timeout, retries)
    seq = 1
    last_progress = time.monotonic()
    for offset in range(0, len(wire), chunk_size):
        part = wire[offset:offset + chunk_size]
        exchange(link, session, b'D', seq, part, timeout, retries)
        seq += 1
        if time.monotonic() - last_progress >= 5:
            print(f'TX {min(offset + len(part), len(wire))}/{len(wire)} B confirmed', flush=True)
            last_progress = time.monotonic()
    exchange(link, session, b'F', seq, b'', timeout, retries)
    print(f'Complete: receiver saved and verified SHA-256 {meta["sha256"]}', flush=True)
    return meta


class Receiver:
    def __init__(self, output_dir):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.session = None
        self.completed = None
        self.last_activity = time.monotonic()

    def handle(self, packet):
        sid, kind, seq, payload = packet
        if kind not in (b'B', b'D', b'F'):
            return None
        if self.completed == (sid, seq) and kind == b'F':
            return sid, b'A', seq, b''
        if sid == self.session and seq == self.next_seq - 1:
            self.last_activity = time.monotonic()
            return sid, b'A', seq, b''  # ACK lost: do not append duplicate data.
        try:
            if kind == b'B' and seq == 0:
                if self.session is not None:
                    raise ValueError('Another transfer is active; wait for its idle timeout')
                meta = decode_metadata(payload)
                name = meta['name']
                if (not isinstance(name, str) or not name or name in ('.', '..')
                        or '/' in name or '\\' in name or '\x00' in name):
                    raise ValueError('Invalid filename')
                for key in ('size', 'wire_size'):
                    if type(meta[key]) is not int or not 0 <= meta[key] <= MAX_SIZE:
                        raise ValueError('File exceeds receiver size limit')
                if meta['codec'] not in ('none', 'zlib'):
                    raise ValueError('Unsupported compression')
                if not isinstance(meta['sha256'], str) or len(meta['sha256']) != 64:
                    raise ValueError('Invalid SHA-256')
                if (self.output_dir / name).exists():
                    raise ValueError(f'Output already exists: {name}')
                self.meta, self.data = meta, bytearray()
                self.session, self.next_seq = sid, 1
                print(f'RX {name}: expecting {meta["wire_size"]} payload bytes', flush=True)
            elif sid != self.session or seq != self.next_seq:
                return None
            elif kind == b'D':
                if len(self.data) + len(payload) > self.meta['wire_size']:
                    raise ValueError('Received more bytes than declared')
                self.data.extend(payload)
                self.next_seq += 1
            elif kind == b'F':
                if len(self.data) != self.meta['wire_size']:
                    raise ValueError('Incomplete file')
                if self.meta['codec'] == 'zlib':
                    decoder = zlib.decompressobj()
                    raw = decoder.decompress(self.data, self.meta['size'] + 1)
                    if not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:
                        raise ValueError('Invalid or oversized compressed file')
                else:
                    raw = bytes(self.data)
                if len(raw) != self.meta['size'] or hashlib.sha256(raw).hexdigest() != self.meta['sha256']:
                    raise ValueError('Size or SHA-256 mismatch')
                dest = self.output_dir / self.meta['name']
                # Publish atomically without overwriting an existing destination.
                temporary = None
                try:
                    with tempfile.NamedTemporaryFile(dir=self.output_dir, prefix='.lora-', delete=False) as f:
                        temporary = Path(f.name)
                        f.write(raw)
                        f.flush()
                        os.fsync(f.fileno())
                    os.link(temporary, dest)
                finally:
                    if temporary is not None:
                        temporary.unlink(missing_ok=True)
                print(f'Saved {dest} ({len(raw)} B), SHA-256 verified', flush=True)
                self.completed = sid, seq
                self.session = None
                self.data.clear()
            else:
                return None
            self.last_activity = time.monotonic()
            return sid, b'A', seq, b''
        except (ValueError, KeyError, TypeError, OSError, zlib.error) as error:
            if sid == self.session:
                self.session = None
                self.data.clear()
            return sid, b'E', seq, str(error).encode()[:500]

    def expire(self, idle_timeout):
        if self.session is not None and time.monotonic() - self.last_activity > idle_timeout:
            print('Incomplete transfer expired; no output file saved', flush=True)
            self.session = None
            self.data.clear()
