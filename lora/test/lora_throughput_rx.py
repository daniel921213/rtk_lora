#!/usr/bin/env python3
"""Measure received raw bytes per second; never send a reply."""

import argparse
import math
import sys
import time

PACKET_SIZE = 58


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', default='/dev/lora')
    parser.add_argument('--baud', type=int, default=115200)
    parser.add_argument('--duration', type=float, default=0,
                        help='Seconds from opening port; 0 runs until Ctrl+C')
    args = parser.parse_args()
    if args.baud <= 0 or not math.isfinite(args.duration) or args.duration < 0:
        parser.error('baud must be positive; duration must be finite and nonnegative')

    try:
        import serial
        with serial.Serial(args.port, args.baud, timeout=0.05, exclusive=True) as port:
            total = window_bytes = 0
            peak = 0.0
            start = window_start = time.monotonic()
            print(f'RX {args.port} baud={args.baud}; counting all received bytes; Ctrl+C to stop',
                  flush=True)
            print(f'Packet equivalents = received bytes / {PACKET_SIZE}; not verified RF packets.',
                  flush=True)
            try:
                while True:
                    data = port.read(min(max(port.in_waiting, 1), 65536))
                    total += len(data)
                    window_bytes += len(data)
                    now = time.monotonic()
                    if now - window_start >= 1:
                        rate = window_bytes / (now - window_start)
                        peak = max(peak, rate)
                        print(f'{now-start:8.2f}s RX {rate:10.1f} B/s {rate*8:10.1f} bit/s '
                              f'{rate/PACKET_SIZE:8.2f} packet_eq/s total={total} B ', flush=True)
                        window_start, window_bytes = now, 0
                    if args.duration and now - start >= args.duration:
                        break
            except KeyboardInterrupt:
                pass
            finally:
                elapsed = max(time.monotonic() - start, 1e-9)
                print(f'RX summary: {total} B / {elapsed:.2f}s = '
                      f'{total/elapsed:.1f} B/s ({total*8/elapsed:.1f} bit/s); '
                      f'{total/elapsed/PACKET_SIZE:.2f} packet_eq/s; '
                      f'complete_58B_blocks={total // PACKET_SIZE}, remainder={total % PACKET_SIZE} B; '
                      f'peak completed window={peak:.1f} B/s', flush=True)
        return 0
    except (ImportError, OSError) as error:
        print(f'Error: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
