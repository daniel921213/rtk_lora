#!/usr/bin/env python3

import serial
import time
import argparse


def parse_rcv(line):
    """
    格式：
    +RCV=<Address>,<Length>,<Data>,<RSSI>,<SNR>
    """

    if not line.startswith("+RCV="):
        return None

    content = line[5:]

    # 前兩個逗號
    first_comma = content.find(",")

    if first_comma == -1:
        return None

    second_comma = content.find(",", first_comma + 1)

    if second_comma == -1:
        return None

    address_str = content[:first_comma]
    length_str = content[first_comma + 1:second_comma]

    try:
        address = int(address_str)
        length = int(length_str)
    except ValueError:
        return None

    data_start = second_comma + 1

    # Data 長度由 Length 決定
    data_end = data_start + length

    if len(content) < data_end:
        return None

    data = content[data_start:data_end]

    remaining = content[data_end:]

    if remaining.startswith(","):
        remaining = remaining[1:]

    fields = remaining.split(",")

    if len(fields) < 2:
        return None

    try:
        rssi = int(fields[0])
        snr = int(fields[1])
    except ValueError:
        rssi = None
        snr = None

    return {
        "address": address,
        "length": length,
        "data": data,
        "data_bytes": len(data.encode("ascii")),
        "rssi": rssi,
        "snr": snr
    }


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

    args = parser.parse_args()

    ser = serial.Serial(
        args.port,
        args.baud,
        timeout=0.1
    )

    total_packets = 0
    total_bytes = 0

    window_packets = 0
    window_bytes = 0

    start_time = time.monotonic()
    window_start = start_time

    print(f"Listening on {args.port} @ {args.baud}")
    print()

    try:

        while True:

            raw = ser.readline()

            if raw:

                try:
                    line = raw.decode(
                        "ascii",
                        errors="ignore"
                    ).strip()

                except Exception:
                    continue

                result = parse_rcv(line)

                if result is not None:

                    received_bytes = result["data_bytes"]

                    total_packets += 1
                    total_bytes += received_bytes

                    window_packets += 1
                    window_bytes += received_bytes

            now = time.monotonic()

            elapsed_window = now - window_start

            # 每秒統計一次
            if elapsed_window >= 1.0:

                rate = window_bytes / elapsed_window

                total_elapsed = now - start_time
                average_rate = total_bytes / total_elapsed

                print(
                    f"RX: "
                    f"{window_packets:4d} pkt/s | "
                    f"{rate:8.1f} B/s | "
                    f"Total: {total_packets:8d} pkt "
                    f"{total_bytes:10d} bytes | "
                    f"Avg: {average_rate:8.1f} B/s"
                )

                window_packets = 0
                window_bytes = 0
                window_start = now

    except KeyboardInterrupt:
        print("\n停止接收")

    finally:
        ser.close()


if __name__ == "__main__":
    main()