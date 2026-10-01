#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import sys
import time

import serial


def checksum(buf: str) -> str:
    csum = 0
    for ch in buf:
        csum ^= ord(ch)
    hi = (csum >> 4) & 0x0F
    lo = csum & 0x0F
    h = chr(hi + ord("A") - 10) if hi >= 10 else chr(hi + ord("0"))
    l = chr(lo + ord("A") - 10) if lo >= 10 else chr(lo + ord("0"))
    return h + l


def send_cmd(ser: serial.Serial, payload: str) -> None:
    msg = f"${payload}*{checksum(payload)}\r\n".encode()
    ser.write(msg)
    ser.flush()


def read_line(ser: serial.Serial, timeout: float = 1.0) -> str:
    old = ser.timeout
    ser.timeout = timeout
    try:
        raw = ser.read_until(b"\n")
    finally:
        ser.timeout = old
    return raw.decode("ascii", errors="ignore").strip()


def wait_pair431_is_one(ser: serial.Serial, timeout_s: float = 8.0) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        line = read_line(ser, timeout=0.5)
        if not line:
            continue
        if line.startswith("$PAIR431,1"):
            return True
        if line.startswith("$PAIR431,0"):
            send_cmd(ser, "PAIR430,1")
            time.sleep(1.0)
            send_cmd(ser, "PAIR431")
    return False


def wait_for_gga(ser: serial.Serial, timeout_s: float = 10.0) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        line = read_line(ser, timeout=0.5)
        if not line:
            continue
        if line.startswith("$GNGGA") or line.startswith("$GPGGA"):
            return True
    return False


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Initialize rover receiver and enforce GGA output")
    parser.add_argument("--port", default="/dev/ttyUSB0", help="rover GNSS serial port")
    parser.add_argument("--baud", type=int, default=115200, help="rover GNSS baud")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.baud != 115200:
        raise SystemExit("baud must be 115200")

    if not os.access(args.port, os.R_OK | os.W_OK):
        raise SystemExit(f"Serial access required: {args.port}; grant access before initialization")

    with serial.Serial(args.port, args.baud, timeout=0.5) as ser:
        send_cmd(ser, "PLSC,MCBASE,0")
        time.sleep(1.0)

        send_cmd(ser, "PAIR431")
        if not wait_pair431_is_one(ser, timeout_s=8.0):
            raise SystemExit("failed to set PAIR431=1 (RTCM input version check)")

        if not wait_for_gga(ser, timeout_s=12.0):
            raise SystemExit("receiver is not outputting valid GGA")

    print(f"[rover-init] receiver ready on {args.port} @ 115200 (GGA verified, PAIR431=1)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
