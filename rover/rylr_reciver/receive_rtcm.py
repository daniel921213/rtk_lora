#!/usr/bin/env python3
"""Extract binary RYLR +RCV payloads; publish CRC-valid RTCM3 via local TCP."""
import argparse
from contextlib import closing
import os
from pathlib import Path
import selectors
import socket
import sys
import time

import serial

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'lora' / 'rylr'))
from rtcm_transport import RTCMParser, RYLRParser


def run(args):
    packets = total = frames = malformed = ignored = 0
    rssi = snr = None
    clients = {}
    wire = RYLRParser()
    rtcm = RTCMParser(timeout=args.frame_timeout)
    with serial.Serial(args.lora_port, args.lora_baud, timeout=0,
                       exclusive=True) as radio, closing(socket.socket()) as listener, \
            selectors.DefaultSelector() as sel:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind((args.listen, args.tcp_port))
        listener.listen(4)
        listener.setblocking(False)
        sel.register(listener, selectors.EVENT_READ)
        # Windows selectors accept sockets but not COM ports. Poll the serial
        # buffer there; keep readiness notification and disconnect detection
        # for POSIX serial devices.
        poll_serial = os.name == 'nt' or not hasattr(radio, 'fileno')
        if not poll_serial:
            sel.register(radio, selectors.EVENT_READ)
        print(f'[rx] source={args.source}; RTCM tcp://{args.listen}:{listener.getsockname()[1]}', flush=True)
        report_at = time.monotonic()

        def drop(client):
            sel.unregister(client)
            client.close()
            del clients[client]

        def process_radio(data):
            nonlocal packets, total, frames, malformed, ignored, rssi, snr
            for event in wire.feed(data):
                if event[0] == 'rcv':
                    _, address, payload, rssi, snr = event
                    if address != args.source:
                        ignored += 1
                        continue
                    packets += 1
                    total += len(payload)
                    for frame in rtcm.feed(payload):
                        frames += 1
                        for client, pending in list(clients.items()):
                            if len(pending) + len(frame) > 8192:
                                print('[rx] disconnect slow TCP client (stale corrections)', flush=True)
                                drop(client)
                            else:
                                pending.extend(frame)
                                sel.modify(client, selectors.EVENT_READ | selectors.EVENT_WRITE)
                elif event[0] == 'malformed':
                    malformed += 1
                    rtcm.buffer.clear()
                elif event[0] == 'line' and event[1].startswith(b'+ERR'):
                    print(f'[rx] module: {event[1]!r}', flush=True)
                    rtcm.buffer.clear()

        try:
            while True:
                for key, mask in sel.select(0.05 if poll_serial else 0.2):
                    obj = key.fileobj
                    if obj is listener:
                        client, peer = listener.accept()
                        client.setblocking(False)
                        clients[client] = bytearray()
                        sel.register(client, selectors.EVENT_READ)
                        print(f'[rx] client connected: {peer}', flush=True)
                    elif obj is radio:
                        data = radio.read(radio.in_waiting or 1)
                        if not data:
                            raise OSError('LoRa serial disconnected')
                        process_radio(data)
                    else:
                        try:
                            if mask & selectors.EVENT_READ:
                                if not obj.recv(4096):
                                    drop(obj)
                                    continue
                            if mask & selectors.EVENT_WRITE:
                                sent = obj.send(clients[obj])
                                if not sent:
                                    drop(obj)
                                    continue
                                del clients[obj][:sent]
                                if not clients[obj]:
                                    sel.modify(obj, selectors.EVENT_READ)
                        except BlockingIOError:
                            pass
                        except OSError:
                            drop(obj)
                if poll_serial:
                    waiting = radio.in_waiting
                    if waiting:
                        process_radio(radio.read(waiting))
                if wire.feed(b''):
                    malformed += 1
                    rtcm.buffer.clear()
                rtcm.feed(b'')
                if time.monotonic() - report_at >= 5:
                    print(f'[rx] packets={packets} raw_bytes={total} valid_frames={frames} '
                          f'CRC_errors={rtcm.crc_errors} discarded_bytes={rtcm.discarded_bytes} '
                          f'malformed={malformed} other_source={ignored} RSSI={rssi} SNR={snr} '
                          f'clients={len(clients)}', flush=True)
                    report_at = time.monotonic()
        finally:
            for client in list(clients):
                drop(client)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--lora-port', default='/dev/lora')
    p.add_argument('--lora-baud', type=int, default=115200)
    p.add_argument('--source', type=int, default=69)
    p.add_argument('--listen', default='127.0.0.1')
    p.add_argument('--tcp-port', type=int, default=2102)
    p.add_argument('--frame-timeout', type=float, default=3)
    args = p.parse_args(argv)
    if not 0 <= args.source <= 65535 or args.frame_timeout <= 0:
        p.error('source must be 0..65535; frame-timeout must be positive')
    try:
        run(args)
    except KeyboardInterrupt:
        return 0
    except (OSError, serial.SerialException) as exc:
        print(f'[rx] error: {exc}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
