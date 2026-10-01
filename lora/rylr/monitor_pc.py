#!/usr/bin/env python3
"""Display binary RYLR packets and CRC-valid RTCM3 frames on Windows or Linux."""

import argparse
import sys
import time

import serial

from rtcm_transport import RYLRParser, RTCMParser


def rtcm_type(frame):
    if len(frame) < 8:
        return None
    return (frame[3] << 4) | (frame[4] >> 4)


def msm_observations(frame):
    """Return satellite, signal and observation counts from an MSM header."""
    message = rtcm_type(frame)
    if (message is None or not 107 <= message // 10 <= 113 or
            not 1 <= message % 10 <= 7 or len(frame) < 28):
        return None
    bits = ''.join(f'{value:08b}' for value in frame[3:-3])
    satellites = bits[73:137].count('1')
    signals = bits[137:169].count('1')
    cells = satellites * signals
    if len(bits) < 169 + cells:
        return None
    observations = bits[169:169 + cells].count('1')
    return satellites, signals, observations


def run(port, baud, source, hex_bytes, diagnose=False):
    wire = RYLRParser()
    rtcm = RTCMParser()
    packets = frames = malformed = empty_msm = nonempty_msm = 0
    raw_bytes = 0
    raw_sample = bytearray()
    last_diagnostic = time.monotonic()
    last_summary = last_diagnostic
    print(f"Listening on {port} at {baud} baud; expected sender address {source}", flush=True)
    with serial.Serial(port, baud, timeout=0.1) as radio:
        while True:
            data = radio.read(radio.in_waiting or 1)
            if diagnose and data:
                raw_bytes += len(data)
                raw_sample.extend(data[:max(0, 32 - len(raw_sample))])
            for event in wire.feed(data):
                if event[0] == "rcv":
                    _, address, payload, rssi, snr = event
                    if address != source:
                        print(f"[other sender] address={address} bytes={len(payload)}", flush=True)
                        continue
                    packets += 1
                    preview = payload[:hex_bytes].hex(" ")
                    print(f"[packet {packets}] bytes={len(payload)} RSSI={rssi} "
                          f"SNR={snr} hex={preview}", flush=True)
                    for frame in rtcm.feed(payload):
                        frames += 1
                        counts = msm_observations(frame)
                        details = ''
                        if counts is not None:
                            satellites, signals, observations = counts
                            if observations:
                                nonempty_msm += 1
                            else:
                                empty_msm += 1
                            details = (f' sats={satellites} sigs={signals} '
                                       f'obs={observations}' +
                                       (' EMPTY' if observations == 0 else ''))
                        print(f"  [RTCM {frames}] type={rtcm_type(frame)} "
                              f"bytes={len(frame)} CRC=OK{details}", flush=True)
                elif event[0] == "malformed":
                    malformed += 1
                    rtcm.buffer.clear()
                    print("[warning] incomplete or malformed +RCV; RTCM reassembly reset", flush=True)
                elif event[0] == "line" and event[1].startswith(b"+ERR"):
                    print(f"[radio] {event[1].decode('ascii', errors='replace')}", flush=True)
                elif diagnose and event[0] == "line":
                    print(f"[radio line] {event[1]!r}", flush=True)
            if wire.feed(b""):
                malformed += 1
                rtcm.buffer.clear()
                print("[warning] +RCV timeout; RTCM reassembly reset", flush=True)
            rtcm.feed(b"")
            now = time.monotonic()
            if now - last_summary >= 5.0:
                print(f"[summary] packets={packets} CRC_OK={frames} "
                      f"CRC_BAD={rtcm.crc_errors} malformed_radio={malformed} "
                      f"MSM_with_obs={nonempty_msm} MSM_empty={empty_msm}",
                      flush=True)
                last_summary = now
            if diagnose and time.monotonic() - last_diagnostic >= 1.0:
                sample = raw_sample.hex(" ") if raw_sample else "-"
                print(f"[serial] bytes/s={raw_bytes} first={sample}", flush=True)
                raw_bytes = 0
                raw_sample.clear()
                last_diagnostic = time.monotonic()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", required=True, help="Windows COM port, e.g. COM5, or Linux /dev/ttyUSB0")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--source", type=int, default=69)
    parser.add_argument("--hex-bytes", type=int, default=32,
                        help="How many bytes of each radio packet to print as hex")
    parser.add_argument("--diagnose", action="store_true",
                        help="Show raw UART byte count and first 32 bytes each second")
    args = parser.parse_args()
    if args.baud <= 0 or not 0 <= args.source <= 65535 or args.hex_bytes < 0:
        parser.error("baud must be positive, source 0..65535, hex-bytes nonnegative")
    try:
        run(args.port, args.baud, args.source, args.hex_bytes, args.diagnose)
    except KeyboardInterrupt:
        print("\nStopped", flush=True)
        return 0
    except (OSError, serial.SerialException) as exc:
        print(f"Serial error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
