#!/usr/bin/env python3
"""Forward RTCM TCP through str2str to GNSS; monitor GGA and record RTK tracks."""
import argparse
import csv
from contextlib import contextmanager
import os
import shutil
import subprocess
from datetime import datetime, timezone
import math
from pathlib import Path
import select
import socket
import sys
import time

import serial


def position(sentence):
    try:
        body, checksum = sentence[1:].split('*')
        actual = 0
        for char in body:
            actual ^= ord(char)
        if actual != int(checksum, 16):
            return None
        p = body.split(',')
        if len(p) < 15 or p[0][2:] != 'GGA' or int(p[6]) not in (4, 5):
            return None
        def degrees(value, hemisphere, latitude):
            number = float(value)
            whole = int(number // 100)
            minutes = number - whole * 100
            result = whole + minutes / 60
            if not math.isfinite(result) or number < 0 or not 0 <= minutes < 60:
                raise ValueError('invalid coordinate')
            if hemisphere not in ('NS' if latitude else 'EW') or len(hemisphere) != 1:
                raise ValueError('invalid hemisphere')
            if result > (90 if latitude else 180):
                raise ValueError('coordinate out of range')
            return -result if hemisphere in ('S', 'W') else result
        lat = degrees(p[2], p[3], True)
        lon = degrees(p[4], p[5], False)
        return [datetime.now(timezone.utc).isoformat(), p[1], lat, lon,
                p[9], p[11], p[6], p[7], p[8], p[13], sentence]
    except (ValueError, IndexError, OverflowError):
        return None


class Str2StrPort:
    """One TCP connection carries RTCM to str2str and relayed GNSS output back."""
    def __init__(self, sock, child):
        self.sock = sock
        self.child = child

    def fileno(self):
        return self.sock.fileno()

    def read(self, size):
        return self.sock.recv(size)

    def write(self, data):
        return self.sock.send(data)

    def check(self):
        if self.child.poll() is not None:
            raise OSError(f'str2str exited with status {self.child.returncode}')


@contextmanager
def open_gnss(args):
    executable = shutil.which(args.str2str)
    if not executable:
        raise OSError(f'str2str not found: {args.str2str}')
    if not os.path.exists(args.gnss_port):
        raise OSError(f'GNSS serial port not found: {args.gnss_port}')
    listener = socket.socket()
    child = connection = None
    try:
        listener.bind(('127.0.0.1', 0))
        listener.listen(1)
        listener.settimeout(0.2)
        # RTKLIB prepends /dev/ to serial names on Linux; retain subdirectories.
        port = os.path.realpath(args.gnss_port)
        if not port.startswith('/dev/'):
            raise OSError('GNSS serial path must resolve inside /dev/')
        port = port[len('/dev/'):]
        command = [executable, '-in', f'tcpcli://127.0.0.1:{listener.getsockname()[1]}',
                   '-out', f'serial://{port}:{args.baud}:8:n:1', '-b', '1', '-r', '1000']
        print('[str2str] ' + ' '.join(command), file=sys.stderr, flush=True)
        child = subprocess.Popen(command, stdout=sys.stderr, stderr=sys.stderr)
        deadline = time.monotonic() + 8
        while connection is None:
            if child.poll() is not None:
                raise OSError(f'str2str startup failed: status {child.returncode}')
            if time.monotonic() >= deadline:
                raise OSError('str2str startup connection timed out')
            try:
                connection, _ = listener.accept()
            except socket.timeout:
                pass
        connection.settimeout(2)
        yield Str2StrPort(connection, child)
    finally:
        if connection is not None:
            connection.close()
        listener.close()
        if child is not None and child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=3)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()


def gga_quality(sentence):
    try:
        body, checksum = sentence[1:].split('*')
        actual = 0
        for char in body:
            actual ^= ord(char)
        if actual != int(checksum, 16):
            return None
        parts = body.split(',')
        if len(parts) < 15 or parts[0][2:] != 'GGA':
            return None
        quality = int(parts[6])
        label = {0: 'NO FIX', 1: 'SINGLE', 2: 'DGPS', 4: 'RTK FIXED', 5: 'RTK FLOAT'}.get(quality, 'OTHER')
        return f'{label}({quality}) satellites={parts[7]} HDOP={parts[8]} correction_age={parts[13] or "n/a"}s'
    except (ValueError, IndexError):
        return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--tcp-port', type=int, default=2102)
    parser.add_argument('--gnss-port', default='/dev/rtk_gps')
    parser.add_argument('--baud', type=int, default=115200)
    parser.add_argument('--str2str', default='str2str', help='RTKLIB str2str executable')
    parser.add_argument('--track', help='New CSV file path (existing files are not overwritten)')
    args = parser.parse_args()
    output = Path(args.track) if args.track else Path(__file__).resolve().parents[2] / 'runtime' / 'tracks' / (datetime.now().strftime('rtk_%Y%m%d_%H%M%S_%f') + '.csv')
    connection = None
    total = points = 0
    retry_at = 0
    quality = 'waiting for valid GGA'
    last_gga = None
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open('x', newline='') as track, open_gnss(args) as gnss:
            writer = csv.writer(track)
            writer.writerow(['received_utc', 'gga_utc_time', 'latitude_deg', 'longitude_deg',
                             'altitude_msl_m', 'geoid_separation_m', 'fix_quality', 'satellites',
                             'hdop', 'correction_age_s', 'raw_gga'])
            track.flush()
            print(f'[track] {output.resolve()} (RTK fixed=4 / float=5 only)', file=sys.stderr, flush=True)
            pending = bytearray()
            last_report = time.monotonic()
            while True:
                gnss.check()
                if connection is None and time.monotonic() >= retry_at:
                    try:
                        connection = socket.create_connection((args.host, args.tcp_port), timeout=1)
                        print(f'[tcp] connected {args.host}:{args.tcp_port}', file=sys.stderr, flush=True)
                    except OSError as exc:
                        print(f'[tcp] {exc}; retry in 3s', file=sys.stderr, flush=True)
                        retry_at = time.monotonic() + 3
                sources = [gnss] + ([connection] if connection is not None else [])
                ready, _, _ = select.select(sources, [], [], 0.2)
                for source in ready:
                    if source is gnss:
                        data = gnss.read(4096)
                        if not data:
                            raise serial.SerialException('GNSS disconnected or another reader is using the port')
                        pending.extend(data)
                        while b'\n' in pending:
                            line, _, remainder = pending.partition(b'\n')
                            pending = bytearray(remainder)
                            try:
                                text = line.strip().decode('ascii')
                            except UnicodeDecodeError:
                                continue
                            if text.startswith('$') and text[3:7] == 'GGA,':
                                print(text, flush=True)
                                status = gga_quality(text)
                                if status is not None:
                                    quality = status
                                    last_gga = time.monotonic()
                                row = position(text)
                                if row is not None:
                                    writer.writerow(row)
                                    track.flush()
                                    points += 1
                        if len(pending) > 4096:
                            pending.clear()
                    else:
                        try:
                            data = connection.recv(4096)
                        except OSError:
                            data = b''
                        if not data:
                            connection.close()
                            connection = None
                            retry_at = time.monotonic() + 3
                            print('[tcp] disconnected; retry in 3s', file=sys.stderr, flush=True)
                            continue
                        offset = 0
                        while offset < len(data):
                            written = gnss.write(data[offset:])
                            if not written:
                                raise serial.SerialException('GNSS write made no progress')
                            offset += written
                            total += written
                if time.monotonic() - last_report >= 5:
                    age = 'never' if last_gga is None else f'{time.monotonic() - last_gga:.1f}s'
                    print(f'[quality] {quality}; last_GGA={age}', file=sys.stderr, flush=True)
                    print(f'[track] TCP={"connected" if connection else "disconnected"} forwarded={total} bytes; RTK points={points}', file=sys.stderr, flush=True)
                    last_report = time.monotonic()
    except KeyboardInterrupt:
        print(f'\n[track] stopped; RTK points={points}; file={output.resolve()}', file=sys.stderr)
    except (OSError, serial.SerialException) as exc:
        print(f'[error] {exc}', file=sys.stderr)
        return 1
    finally:
        if connection is not None:
            connection.close()
    return 0


if __name__ == '__main__':
    sys.exit(main())
