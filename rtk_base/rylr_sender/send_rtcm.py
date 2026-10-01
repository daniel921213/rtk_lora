#!/usr/bin/env python3
"""Send validated RTCM3 frames as raw binary AT+SEND payloads to address 67."""
import argparse
from contextlib import ExitStack
from pathlib import Path
import os
import socket
import sys
import time

import serial

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'lora' / 'rylr'))
from rtcm_transport import RTCMParser, RYLRParser, send_command, wait_ok, write_all


def run(args):
    frames = packets = total = 0
    with ExitStack() as stack:
        radio = stack.enter_context(serial.Serial(args.lora_port, args.lora_baud,
                         timeout=0.05, write_timeout=2, exclusive=True))
        replies = RYLRParser()
        # Drain old UART replies before the initial handshake only.
        radio.reset_input_buffer()
        write_all(radio, b'AT\r\n')
        wait_ok(radio, replies, args.ok_timeout)
        if args.tcp_host:
            source = stack.enter_context(socket.create_connection((args.tcp_host, args.tcp_port), timeout=5))
            source.settimeout(0.2)
            read = lambda: source.recv(4096)
        else:
            source = stack.enter_context(serial.Serial(args.gnss_port, args.gnss_baud,
                              timeout=0.2, exclusive=True))
            read = lambda: source.read(min(source.in_waiting or 1, 4096))
        parser = RTCMParser()
        report_at = time.monotonic()
        print(f'[tx] destination={args.dest}; raw payload <= {args.payload_size} bytes', flush=True)
        while True:
            try:
                data = read()
            except socket.timeout:
                data = None
            if args.tcp_host and data == b'':
                raise ConnectionError('RTCM TCP source disconnected; restart sender after source is restored')
            for frame in parser.feed(data or b''):
                for offset in range(0, len(frame), args.payload_size):
                    payload = frame[offset:offset + args.payload_size]
                    write_all(radio, send_command(args.dest, payload))
                    wait_ok(radio, replies, args.ok_timeout)
                    packets += 1
                    total += len(payload)
                    if args.packet_gap:
                        time.sleep(args.packet_gap)
                frames += 1
            if time.monotonic() - report_at >= 5:
                print(f'[tx] frames={frames} packets_OK={packets} RTCM_bytes={total} '
                      f'CRC_errors={parser.crc_errors} discarded_bytes={parser.discarded_bytes}', flush=True)
                report_at = time.monotonic()


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--gnss-port', default='/dev/rtk_gps')
    p.add_argument('--gnss-baud', type=int, default=115200)
    p.add_argument('--tcp-host', help='Read existing RTCM TCP server instead of GNSS serial')
    p.add_argument('--tcp-port', type=int, default=2101)
    p.add_argument('--lora-port', default='/dev/lora')
    p.add_argument('--lora-baud', type=int, default=115200)
    p.add_argument('--dest', type=int, default=67)
    p.add_argument('--payload-size', type=int, default=240)
    p.add_argument('--ok-timeout', type=float, default=3)
    p.add_argument('--packet-gap', type=float, default=0.01)
    args = p.parse_args(argv)
    if not 0 <= args.dest <= 65535 or not 1 <= args.payload_size <= 240:
        p.error('dest must be 0..65535; payload-size must be 1..240')
    if args.ok_timeout <= 0 or args.packet_gap < 0:
        p.error('ok-timeout must be positive; packet-gap must be nonnegative')
    if not args.tcp_host and os.path.realpath(args.gnss_port) == os.path.realpath(args.lora_port):
        p.error('GNSS and LoRa ports must differ')
    try:
        run(args)
    except KeyboardInterrupt:
        return 0
    except (OSError, serial.SerialException, RuntimeError) as exc:
        print(f'[tx] error: {exc}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
