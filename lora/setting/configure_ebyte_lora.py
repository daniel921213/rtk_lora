#!/usr/bin/env python3
"""E32-170T30D configuration via pyserial; M0 and M1 must both be HIGH.

Read:  python3 configure_ebyte_lora.py --config-mode
Write: python3 configure_ebyte_lora.py --config-mode --write

Writes the agreed profile: 115200 8N1, 9.6 kbps, 170 MHz, address 0,
transparent mode, 30 dBm, FEC off, push-pull IO, permanent storage.
After configuration, set M0/M1 LOW and use 115200 8N1 for communication.
"""

import argparse
import datetime
import json
from pathlib import Path
import sys
import time


PROFILE = bytes.fromhex("C0 00 00 3D 28 40")


def exchange(port, command, size):
    port.reset_input_buffer()
    port.write(command)
    port.flush()
    response = port.read(size)
    if len(response) != size:
        raise RuntimeError(
            "Incomplete response: " + response.hex(" ")
            + "; check M0/M1 HIGH, power, common ground and TX/RX wiring."
        )
    time.sleep(0.1)
    return response


def read_config(port):
    result = exchange(port, bytes.fromhex("C1 C1 C1"), 6)
    if result[0] not in (0xC0, 0xC2):
        raise RuntimeError("Invalid configuration response: " + result.hex(" "))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", default="/dev/lora")
    parser.add_argument("--config-mode", action="store_true",
                        help="Confirm M0 and M1 are both HIGH")
    parser.add_argument("--write", action="store_true",
                        help="Permanently write the fixed profile; otherwise read only")
    parser.add_argument("--backup-dir", type=Path, default=Path.home() / "lora-backups")
    args = parser.parse_args()
    if not args.config_mode:
        parser.error("Set M0 and M1 HIGH, then specify --config-mode.")
    try:
        import serial
        with serial.Serial(args.port, 9600, bytesize=8, parity="N", stopbits=1,
                           timeout=2, write_timeout=2, exclusive=True) as port:
            time.sleep(0.2)
            version = exchange(port, bytes.fromhex("C3 C3 C3"), 4)
            print("Version:", version.hex(" "))
            if version[:2] != bytes.fromhex("C3 46"):
                raise RuntimeError("Expected E32 170 MHz model (C3 46); refusing to continue.")
            original = read_config(port)
            print("Current:", original.hex(" "))
            print("Target: ", PROFILE.hex(" "))
            if not args.write:
                print("Read only; no parameters changed.")
                return 0
            args.backup_dir.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            backup = args.backup_dir / ("e32-" + timestamp + ".json")
            with backup.open("x") as file:
                json.dump({"port": args.port, "version": version.hex(" "),
                           "original": original.hex(" "), "target": PROFILE.hex(" ")},
                          file, indent=2)
                file.write("\n")
            print("Backup:", backup)
            reply = exchange(port, PROFILE, 6)
            if reply != PROFILE:
                raise RuntimeError("Write reply mismatch: " + reply.hex(" ")
                                   + "; configuration may have changed; read again.")
            actual = read_config(port)
            print("Readback:", actual.hex(" "))
            if actual != PROFILE:
                raise RuntimeError("Readback mismatch; configuration may have changed.")
            print("Permanent write verified. Set M0/M1 LOW; use 115200 8N1.")
        return 0
    except (ImportError, OSError, RuntimeError) as error:
        print("Error:", error, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
