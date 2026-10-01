#!/usr/bin/env python3
"""Forward raw LoRa bytes to GNSS and print GGA using one GNSS handle."""
import argparse
import os
import select
import sys
import time

import serial


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--lora-port', default='/dev/ttyUSB0')
    parser.add_argument('--gnss-port', default='/dev/ttyUSB1')
    parser.add_argument('--lora-baud', type=int, default=115200)
    parser.add_argument('--gnss-baud', type=int, default=115200)
    args = parser.parse_args()
    if os.path.realpath(args.lora_port) == os.path.realpath(args.gnss_port):
        parser.error('LoRa and GNSS must use different ports')
    total = 0
    try:
        with serial.Serial(args.lora_port, args.lora_baud, timeout=0, exclusive=True) as lora, \
             serial.Serial(args.gnss_port, args.gnss_baud, timeout=0,
                           write_timeout=2, exclusive=True) as gnss:
            print(f'[bridge] {args.lora_port} -> {args.gnss_port}; GGA on stdout; Ctrl+C to stop', file=sys.stderr, flush=True)
            pending = bytearray()
            last_report = time.monotonic()
            interval_bytes = 0
            while True:
                ready, _, _ = select.select([lora, gnss], [], [], 0.5)
                for source in ready:
                    data = source.read(4096)
                    if not data:
                        raise serial.SerialException(f'{source.port}: no data after readiness; disconnected or another reader')
                    if source is lora:
                        offset = 0
                        while offset < len(data):
                            written = gnss.write(data[offset:])
                            if not written:
                                raise serial.SerialException('GNSS write made no progress')
                            offset += written
                            total += written
                            interval_bytes += written
                    else:
                        pending.extend(data)
                        while b'\n' in pending:
                            line, _, remainder = pending.partition(b'\n')
                            pending = bytearray(remainder)
                            line = line.strip()
                            if line.startswith(b'$') and line[3:7] == b'GGA,':
                                try:
                                    text = line.decode('ascii')
                                except UnicodeDecodeError:
                                    continue
                                print(text, flush=True)
                        if len(pending) > 4096:
                            pending.clear()
                now = time.monotonic()
                if now - last_report >= 5:
                    print(f'[bridge] LoRa -> GNSS: {interval_bytes} bytes / {now-last_report:.1f}s; total={total}', file=sys.stderr, flush=True)
                    last_report = now
                    interval_bytes = 0
    except KeyboardInterrupt:
        print(f'\n[bridge] stopped; forwarded {total} bytes', file=sys.stderr)
        return 0
    except (serial.SerialException, OSError) as exc:
        print(f'[bridge] error: {exc}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
