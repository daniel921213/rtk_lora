#!/usr/bin/env python3
"""Send a raw byte stream through a transparent LoRa serial module."""

import argparse
import math
import sys
import time

PACKET_SIZE = 58


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', default='/dev/ttyUSB0')
    parser.add_argument('--baud', type=int, default=115200)
    rate_group = parser.add_mutually_exclusive_group()
    rate_group.add_argument('--pps', '--packets-per-second', dest='pps', type=float,
                            help='Packets/second (default: 10); 0 sends without pacing')
    rate_group.add_argument('--rate', type=float,
                            help='Legacy bytes/second target; mutually exclusive with --pps')
    parser.add_argument('--duration', type=float, default=60,
                        help='Seconds to run; 0 runs until Ctrl+C')
    args = parser.parse_args()
    if args.pps is None:
        args.pps = args.rate / PACKET_SIZE if args.rate is not None else 10.0
    if (args.baud <= 0 or
            not math.isfinite(args.pps) or args.pps < 0 or
            not math.isfinite(args.duration) or args.duration < 0):
        parser.error('baud must be positive; pps/rate/duration must be finite and nonnegative')

    try:
        import serial
        with serial.Serial(args.port, args.baud, timeout=0.1,
                           write_timeout=1, exclusive=True) as port:
            # Fixed raw data only: no framing, sequence numbers, ACK or retries.
            size = PACKET_SIZE
            payload = (b'0123456789ABCDEF' * ((size + 15) // 16))[:size]
            total = window_bytes = 0
            writes = window_writes = 0
            start = window_start = next_send = time.monotonic()
            print(f'TX {args.port} baud={args.baud} write_size={size} B target={args.pps:g} packets/s '
                  f'({args.pps * size:g} B/s) '
                  '(0=unlimited); counts UART writes, not radio delivery', flush=True)
            print('UART write boundaries do not guarantee separate LoRa radio packets.', flush=True)
            try:
                while True:
                    now = time.monotonic()
                    if args.duration and now - start >= args.duration:
                        break
                    if now - window_start >= 1:
                        elapsed = now - window_start
                        rate = window_bytes / elapsed
                        print(f'{now-start:8.2f}s TX {rate:10.1f} B/s {rate*8:10.1f} bit/s '
                              f'{window_writes / elapsed:8.2f} packets/s total={total} B writes={writes}', flush=True)
                        window_start, window_bytes, window_writes = now, 0, 0
                    if args.pps and now < next_send:
                        time.sleep(min(next_send - now, 0.02))
                        continue
                    written = port.write(payload)
                    total += written
                    window_bytes += written
                    if written != len(payload):
                        raise OSError('Partial UART write; stopping without retry')
                    writes += 1
                    window_writes += 1
                    if args.pps:
                        # Do not send a catch-up burst after a slow write.
                        next_send = max(next_send + 1.0 / args.pps, time.monotonic())
            except KeyboardInterrupt:
                pass
            finally:
                elapsed = max(time.monotonic() - start, 1e-9)
                print(f'TX summary: {total} B / {elapsed:.2f}s = '
                      f'{total/elapsed:.1f} B/s ({total*8/elapsed:.1f} bit/s); '
                      f'{writes/elapsed:.2f} packets/s; complete_writes={writes}, write_size={size} B', flush=True)
        return 0
    except (ImportError, OSError) as error:
        print(f'Error: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
