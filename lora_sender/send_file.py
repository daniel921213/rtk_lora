#!/usr/bin/env python3
"""Send trajectory JSON with exactly one sample per frame, at most 58 bytes."""
import argparse
import math
from pathlib import Path
import sys
from point_protocol import Link, prepare, send_points, encode

DEFAULT_FILE = Path(__file__).resolve().parent / 'example_files' / 'rosbag2_2026_02_25-12_40_24_0_rtk_trajectory.json'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('file', nargs='?', type=Path, default=DEFAULT_FILE)
    parser.add_argument('--port', default='/dev/lora')
    parser.add_argument('--baud', type=int, default=115200)
    parser.add_argument('--rate', type=float, default=200, help='UART byte/s; 0 = unlimited')
    parser.add_argument('--turn-delay', type=float, default=0.5, help='Guard seconds before each UART write')
    parser.add_argument('--ack-timeout', type=float, default=10)
    parser.add_argument('--retries', type=int, default=5)
    parser.add_argument('--dry-run', action='store_true', help='Validate all frames without opening serial')
    args = parser.parse_args()
    if (args.baud <= 0 or args.retries < 0 or any(not math.isfinite(v) or v < 0 for v in (args.rate, args.turn_delay))
            or not math.isfinite(args.ack_timeout) or args.ack_timeout <= 0):
        parser.error('Invalid serial/timing parameters')
    try:
        packets = prepare(args.file)
        if args.dry_run:
            print(f'{len(packets)-5} points; {len(packets)} frames; max={max(len(encode(*p)) for p in packets)} B; total={sum(len(encode(*p)) for p in packets)} B before ACK/retries')
            for packet in packets[:5]:
                print(repr(encode(*packet)))
            return 0
        import serial
        with serial.Serial(args.port, args.baud, timeout=0.1, write_timeout=5, exclusive=True) as port:
            send_points(Link(port, args.rate, args.turn_delay), packets, args.ack_timeout, args.retries)
        return 0
    except (ImportError, OSError, ValueError, KeyError, TypeError, RuntimeError) as error:
        print(f'Error: {error}', file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == '__main__':
    sys.exit(main())
