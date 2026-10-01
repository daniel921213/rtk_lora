#!/usr/bin/env python3
"""Forward raw RTK bytes from a serial input to STM32 USART3."""

import argparse
import os
import sys
import socket
from contextlib import ExitStack

import serial


class TCPBroadcast:
    """Nonblocking fan-out; disconnect slow clients rather than stall USART3."""

    def __init__(self, host, port):
        self.listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.clients = {}
        try:
            self.listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.listener.bind((host, port))
            self.listener.listen(16)
            self.listener.setblocking(False)
        except BaseException:
            self.listener.close()
            raise

    def pump(self, data=b''):
        # Existing clients receive this chunk; new clients start with the next.
        for client, pending in list(self.clients.items()):
            pending.extend(data)
            try:
                if len(pending) > 262144:
                    raise ConnectionError('TCP client is too slow')
                if pending:
                    sent = client.send(pending)
                    if sent == 0:
                        raise ConnectionError('TCP client disconnected')
                    del pending[:sent]
            except BlockingIOError:
                pass
            except OSError:
                client.close()
                del self.clients[client]
        for _ in range(16):
            try:
                client, _ = self.listener.accept()
            except BlockingIOError:
                break
            if len(self.clients) >= 16:
                client.close()
                continue
            client.setblocking(False)
            self.clients[client] = bytearray()

    def close(self):
        for client in self.clients:
            client.close()
        self.listener.close()


def forward(source, destination, tcp=None):
    """Preserve all bytes, including binary data and whitespace."""
    while True:
        if tcp is not None:
            tcp.pump()
        data = source.read(min(source.in_waiting or 1, 4096))
        pending = memoryview(data)
        while pending:
            written = destination.write(pending)
            if not written:
                raise serial.SerialTimeoutException("Output made no progress")
            pending = pending[written:]
        if tcp is not None:
            tcp.pump(data)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input-port', default='/dev/rtk_gps',
                        help='RTK input serial port (default: /dev/rtk_gps)')
    parser.add_argument('--output-port', default='/dev/ttySTM1',
                        help='USART3 serial port (default: /dev/ttySTM1)')
    parser.add_argument('--input-baud', type=int, default=115200)
    parser.add_argument('--output-baud', type=int, default=115200)
    parser.add_argument('--tcp-host', default='0.0.0.0')
    parser.add_argument('--tcp-port', type=int, default=0,
                        help='TCP broadcast port; 0 disables TCP (default)')
    args = parser.parse_args()
    if not 0 <= args.tcp_port <= 65535:
        parser.error('TCP port must be between 0 and 65535')
    if args.input_baud <= 0 or args.output_baud <= 0:
        parser.error('Baud rates must be positive')
    if os.path.realpath(args.input_port) == os.path.realpath(args.output_port):
        parser.error('Input and output must be different serial ports')

    try:
        # 8N1, no software or hardware flow control: XON/XOFF bytes are data.
        with ExitStack() as stack, serial.Serial(args.input_port, args.input_baud, timeout=0.2,
                           exclusive=True, xonxoff=False, rtscts=False,
                           dsrdtr=False) as source, serial.Serial(
                args.output_port, args.output_baud, timeout=0.2,
                write_timeout=5, exclusive=True, xonxoff=False,
                rtscts=False, dsrdtr=False) as destination:
            print(f'Forwarding raw bytes: {source.port} ({source.baudrate}) -> '
                  f'{destination.port} ({destination.baudrate}), 8N1',
                  file=sys.stderr, flush=True)
            tcp = None
            if args.tcp_port:
                tcp = TCPBroadcast(args.tcp_host, args.tcp_port)
                stack.callback(tcp.close)
                print(f'TCP broadcast: {args.tcp_host}:{args.tcp_port}',
                      file=sys.stderr, flush=True)
            forward(source, destination, tcp)
    except KeyboardInterrupt:
        print('Stopped', file=sys.stderr)
        return 0
    except (serial.SerialException, OSError) as exc:
        # Do not retry an uncertain partial write: that could duplicate bytes.
        print(f'Forwarding stopped: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
