#!/usr/bin/env python3

import serial
import time
import argparse


DEST_ADDRESS = 69
PAYLOAD_SIZE = 200


def make_payload():
    return "A" * PAYLOAD_SIZE


def wait_ok(ser, timeout=2.0):
    """
    等待 RYLR998 回覆 +OK
    回傳:
        True  -> 收到 +OK
        False -> timeout 或收到 ERR
    """

    start = time.monotonic()

    while time.monotonic() - start < timeout:

        if ser.in_waiting:
            raw = ser.readline()

            try:
                line = raw.decode(
                    "ascii",
                    errors="ignore"
                ).strip()
            except Exception:
                continue

            if not line:
                continue

            # Debug
            print(f"  <- {line}")

            if "+OK" in line:
                return True

            if "+ERR" in line or "ERR" in line:
                return False

        time.sleep(0.001)

    return False


def main():

    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--port",
        default="/dev/lora",
        help="Serial port"
    )

    parser.add_argument(
        "--baud",
        type=int,
        default=115200,
        help="UART baud rate"
    )

    parser.add_argument(
        "--pps",
        type=float,
        default=1.0,
        help="Maximum packets per second"
    )

    parser.add_argument(
        "--timeout",
        type=float,
        default=2.0,
        help="Timeout waiting for +OK"
    )

    args = parser.parse_args()

    if args.pps <= 0:
        raise ValueError("pps 必須 > 0")

    ser = serial.Serial(
        args.port,
        args.baud,
        timeout=0.05
    )

    payload = make_payload()

    payload_bytes = payload.encode("ascii")

    if len(payload_bytes) != PAYLOAD_SIZE:
        raise RuntimeError("Payload size 不等於 200 bytes")

    interval = 1.0 / args.pps

    total_sent = 0
    total_failed = 0

    window_sent = 0
    window_bytes = 0

    stat_start = time.monotonic()

    print(f"Port        : {args.port}")
    print(f"Baud        : {args.baud}")
    print(f"Destination : {DEST_ADDRESS}")
    print(f"Payload     : {PAYLOAD_SIZE} bytes")
    print(f"Max PPS     : {args.pps}")
    print(f"Target rate : {args.pps * PAYLOAD_SIZE:.1f} B/s")
    print(f"OK timeout  : {args.timeout} s")
    print()

    try:

        while True:

            cycle_start = time.monotonic()

            # 避免上一輪殘留資料干擾
            ser.reset_input_buffer()

            command = (
                f"AT+SEND={DEST_ADDRESS},"
                f"{PAYLOAD_SIZE},"
                f"{payload}\r\n"
            )

            # 發送 AT+SEND
            ser.write(command.encode("ascii"))
            ser.flush()

            print(
                f"-> SEND #{total_sent + total_failed + 1}"
            )

            # 等 +OK
            success = wait_ok(
                ser,
                timeout=args.timeout
            )

            if success:

                total_sent += 1
                window_sent += 1
                window_bytes += PAYLOAD_SIZE

            else:

                total_failed += 1
                print("!! 沒收到 +OK / 發送失敗")

            now = time.monotonic()

            # 每秒統計
            elapsed = now - stat_start

            if elapsed >= 1.0:

                rate = window_bytes / elapsed
                actual_pps = window_sent / elapsed

                print(
                    "\n"
                    f"[TX STAT] "
                    f"{actual_pps:.2f} pkt/s | "
                    f"{rate:.1f} B/s | "
                    f"OK={total_sent} | "
                    f"FAIL={total_failed}"
                    "\n"
                )

                window_sent = 0
                window_bytes = 0
                stat_start = now

            # ------------------------------------------------
            # PPS 限制
            # ------------------------------------------------

            used_time = time.monotonic() - cycle_start

            remaining = interval - used_time

            if remaining > 0:
                time.sleep(remaining)

    except KeyboardInterrupt:
        print("\n停止發送")

    finally:

        ser.close()

        print()
        print("===== Summary =====")
        print(f"Successful : {total_sent}")
        print(f"Failed     : {total_failed}")
        print(f"Payload    : {total_sent * PAYLOAD_SIZE} bytes")


if __name__ == "__main__":
    main()