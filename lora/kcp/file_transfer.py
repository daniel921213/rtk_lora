#!/usr/bin/env python3
"""Transfer a file over KCP/LoRa and confirm its size and SHA-256 at the peer."""
import argparse
import hashlib
import math
import os
from pathlib import Path
import struct
import sys
import tempfile
import time

try:
    from .transport import KcpLink
    from .radio_io import RadioIO
except ImportError:
    from transport import KcpLink
    from radio_io import RadioIO

MAGIC = b'KFT1'
CHUNK_SIZE = 4096
MAX_FILE = 1024 * 1024 * 1024


class FileSender:
    def __init__(self, path):
        self.source = open(path, 'rb')
        self.size = os.fstat(self.source.fileno()).st_size
        if self.size > MAX_FILE:
            self.source.close()
            raise ValueError('File exceeds 1 GiB limit')
        self.count = 0
        self.digest = hashlib.sha256()
        self.stage = 'header'
        self.verified = False

    def next_message(self):
        if self.stage == 'header':
            self.stage = 'data'
            return MAGIC + b'H' + struct.pack('<Q', self.size)
        if self.stage == 'data':
            data = self.source.read(CHUNK_SIZE)
            if data:
                self.count += len(data)
                if self.count > self.size:
                    raise ValueError('Source size changed during transfer')
                self.digest.update(data)
                return MAGIC + b'D' + data
            if self.count != self.size:
                raise ValueError('Source size changed during transfer')
            self.stage = 'verify'
            return MAGIC + b'E' + self.digest.digest()
        return None

    def accept(self, message):
        if self.stage != 'verify' or message != MAGIC + b'V' + self.digest.digest():
            raise ValueError('Unexpected or mismatched receiver verification')
        self.verified = True

    def close(self):
        self.source.close()


class FileReceiver:
    def __init__(self, path):
        self.path = Path(path)
        if self.path.exists():
            raise FileExistsError(f'Output exists: {path}')
        self.temp = None
        self.target = None
        self.size = None
        self.count = 0
        self.digest = hashlib.sha256()
        self.verified = False

    def accept(self, message):
        if not message.startswith(MAGIC) or len(message) < 5 or self.verified:
            raise ValueError('Invalid file transfer message/state')
        kind, data = message[4:5], message[5:]
        if kind == b'H' and self.size is None and len(data) == 8:
            self.size, = struct.unpack('<Q', data)
            if self.size > MAX_FILE:
                raise ValueError('File exceeds 1 GiB limit')
            fd, name = tempfile.mkstemp(prefix='.' + self.path.name + '.', suffix='.part',
                                        dir=self.path.parent)
            self.temp = Path(name)
            self.target = os.fdopen(fd, 'wb')
        elif kind == b'D' and self.target is not None and 0 < len(data) <= CHUNK_SIZE:
            if self.count + len(data) > self.size:
                raise ValueError('Received more than declared size')
            self.target.write(data)
            self.digest.update(data)
            self.count += len(data)
        elif kind == b'E' and self.target is not None and len(data) == 32:
            if self.count != self.size or data != self.digest.digest():
                raise ValueError('File size or SHA-256 mismatch')
            self.target.flush()
            os.fsync(self.target.fileno())
            self.target.close()
            self.target = None
            # Atomic no-clobber publication, on the same filesystem.
            os.link(self.temp, self.path)
            self.temp.unlink()
            self.temp = None
            self.verified = True
            return MAGIC + b'V' + data
        else:
            raise ValueError('Unexpected file transfer message')
        return None

    def close(self):
        if self.target:
            self.target.close()
        if self.temp:
            self.temp.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['send', 'receive'])
    parser.add_argument('--file', required=True, help='Source file / new destination file')
    parser.add_argument('--port', default='/dev/lora')
    parser.add_argument('--radio', choices=['transparent', 'rylr'], default='transparent')
    parser.add_argument('--peer', type=int, help='RYLR address of the remote peer')
    parser.add_argument('--baud', type=int, default=115200)
    parser.add_argument('--conv', type=lambda value: int(value, 0), required=True)
    parser.add_argument('--pps', type=float, default=2)
    parser.add_argument('--rto-ms', type=int, default=3000)
    parser.add_argument('--timeout', type=float, default=1800)
    args = parser.parse_args()
    if (args.baud <= 0 or not 0 <= args.conv <= 0xffffffff or
            not math.isfinite(args.pps) or args.pps <= 0 or
            not math.isfinite(args.timeout) or args.timeout <= 0 or
            not 100 <= args.rto_ms <= 60000 or
            (args.radio == 'rylr' and (args.peer is None or not 0 <= args.peer <= 65535))):
        parser.error('Invalid baud, conv, pps, timeout or rto-ms')
    transfer = None
    try:
        import serial
        transfer = FileSender(args.file) if args.mode == 'send' else FileReceiver(args.file)
        with KcpLink(args.conv, args.rto_ms) as link, serial.Serial(
                args.port, args.baud, timeout=0, write_timeout=1, exclusive=True) as port:
            start = next_send = next_report = time.monotonic()
            verified_at = None
            tx_bytes = rx_bytes = frames = 0
            radio = RadioIO(port, args.radio, args.peer)
            # Delimiter only: recover from a stale, incomplete serial frame.
            if args.radio == 'transparent' and port.write(b'\0') != 1:
                raise OSError('Partial serial write')
            print(f'{args.mode} {args.file} conv={args.conv} pps={args.pps:g}', flush=True)
            while time.monotonic() - start < args.timeout:
                now = time.monotonic()
                data = radio.poll(now)
                rx_bytes += len(data)
                link.feed(data)
                for message in link.receive():
                    response = transfer.accept(message)
                    if response is not None:
                        link.send(response)
                if args.mode == 'send' and not link.pending:
                    message = transfer.next_message()
                    if message is not None:
                        link.send(message)
                if not link.outgoing:
                    link.update(int(now * 1000))
                if link.outgoing and radio.ready and now >= next_send:
                    frame = link.outgoing.popleft()
                    radio.send(frame, now)
                    tx_bytes += len(frame)
                    frames += 1
                    next_send = time.monotonic() + 1 / args.pps
                if transfer.verified and verified_at is None:
                    verified_at = now
                    print(f'SHA256 verified: {transfer.digest.hexdigest()} bytes={transfer.count}',
                          flush=True)
                if now >= next_report:
                    print(f'{now-start:.1f}s data={transfer.count}/{transfer.size} '
                          f'tx={tx_bytes} rx={rx_bytes} frames={frames} '
                          f'bad_frames={link.decoder.errors} at_errors={radio.errors} pending={link.pending}', flush=True)
                    next_report = now + 10
                if verified_at is not None and not link.pending and not link.outgoing and radio.ready:
                    # Sender stays available to ACK a retransmitted verification reply.
                    linger = max(2, 2 * args.rto_ms / 1000 + 1)
                    if args.mode == 'receive' or now - verified_at >= linger:
                        print(f'COMPLETE elapsed={now-start:.2f}s bytes={transfer.count} '
                              f'sha256={transfer.digest.hexdigest()} tx={tx_bytes} rx={rx_bytes} '
                              f'bad_frames={link.decoder.errors}', flush=True)
                        return 0
                time.sleep(.005)
            raise TimeoutError('Session timeout: file transfer not confirmed')
    except KeyboardInterrupt:
        return 130
    except (ImportError, OSError, ValueError, RuntimeError, BufferError) as exc:
        print(f'Error: {exc}', file=sys.stderr, flush=True)
        return 1
    finally:
        if transfer is not None:
            transfer.close()


if __name__ == '__main__':
    sys.exit(main())
