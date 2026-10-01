#!/usr/bin/env python3
"""Receive one-point frames and reassemble trajectory JSON on STM32MP Linux."""
import argparse
import math
from pathlib import Path
import sys
import time
from point_protocol import Link, Receiver


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', default='/dev/lora')
    parser.add_argument('--baud', type=int, default=115200)
    parser.add_argument('--output-dir', type=Path, default=Path(__file__).resolve().parent / 'received_files')
    parser.add_argument('--rate', type=float, default=200)
    parser.add_argument('--turn-delay', type=float, default=0.5)
    parser.add_argument('--idle-timeout', type=float, default=120)
    parser.add_argument('--verbose', action='store_true')
    args = parser.parse_args()
    if (args.baud <= 0 or any(not math.isfinite(v) or v < 0 for v in (args.rate, args.turn_delay))
            or not math.isfinite(args.idle_timeout) or args.idle_timeout <= 0):
        parser.error('Invalid serial/timing parameters')
    try:
        import serial
        receiver = Receiver(args.output_dir)
        with serial.Serial(args.port, args.baud, timeout=.1, write_timeout=5, exclusive=True) as port:
            link = Link(port, args.rate, args.turn_delay)
            print(f'Point mode, max 58 B; listening {args.port}; output={args.output_dir}', flush=True)
            last = time.monotonic()
            while True:
                receiver.expire(args.idle_timeout)
                packet = link.receive(.5)
                if packet:
                    reply = receiver.handle(packet)
                    if reply:
                        link.send(*reply)
                        if args.verbose:
                            print(f'TX {reply[1].decode()} seq={reply[2]} session={reply[0].decode()}', flush=True)
                if time.monotonic() - last >= 15:
                    print(f'RX bytes={link.received_bytes}, frames={link.received_frames}, points={len(receiver.points)}', flush=True)
                    last = time.monotonic()
    except KeyboardInterrupt:
        return 0
    except (ImportError, OSError, ValueError) as error:
        print(f'Error: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
