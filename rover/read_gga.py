#!/usr/bin/env python3
"""Print raw GNSS GGA sentences without ROS or receiver configuration."""

import argparse
import sys

import serial


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", default="/dev/ttyUSB1", help="GNSS serial port")
    parser.add_argument("--baud", type=int, default=115200, help="GNSS baud rate")
    args = parser.parse_args()

    try:
        with serial.Serial(args.port, args.baud, timeout=1) as port:
            print(f"Reading GGA from {args.port} @ {args.baud}; Ctrl+C to stop", file=sys.stderr)
            pending = bytearray()
            while True:
                chunk = port.read_until(b"\n")
                if not chunk:
                    continue
                pending.extend(chunk)
                if not pending.endswith(b"\n"):
                    if len(pending) > 4096:
                        pending.clear()
                    continue
                line = bytes(pending).strip()
                pending.clear()
                # Accept any two-letter NMEA talker, e.g. GN, GP, GA, GB.
                if len(line) >= 7 and line[:1] == b"$" and line[3:7] == b"GGA,":
                    try:
                        print(line.decode("ascii"), flush=True)
                    except UnicodeDecodeError:
                        continue
    except KeyboardInterrupt:
        return 0
    except (serial.SerialException, OSError) as exc:
        print(f"Serial error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
