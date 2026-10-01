#!/usr/bin/env python3
"""Send/receive one binary message through bidirectional transparent LoRa UART."""
import argparse
import math
from pathlib import Path
import sys
import time

from transport import KcpLink, MAX_MESSAGE


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['send', 'receive'])
    parser.add_argument('--port', required=True)
    parser.add_argument('--baud', type=int, default=115200)
    parser.add_argument('--conv', type=lambda x: int(x, 0), required=True,
                        help='Same fresh uint32 session ID on both peers')
    parser.add_argument('--file', required=True, help='Input file / new output file')
    parser.add_argument('--pps', type=float, default=5,
                        help='Maximum UART frames/s, including ACK (default: 5)')
    parser.add_argument('--rto-ms', type=int, default=1500)
    parser.add_argument('--timeout', type=float, default=120,
                        help='Overall session seconds; receiver keeps ACKing until then')
    args = parser.parse_args()
    if (args.baud <= 0 or not math.isfinite(args.pps) or args.pps <= 0 or
            not math.isfinite(args.timeout) or args.timeout <= 0 or
            not 0 <= args.conv <= 0xffffffff or not 100 <= args.rto_ms <= 60000):
        parser.error('Invalid baud, pps, timeout, conv, or rto-ms')
    try:
        import serial
        payload = None
        if args.mode == 'send':
            with open(args.file, 'rb') as source:
                payload = source.read(MAX_MESSAGE + 1)
            if len(payload) > MAX_MESSAGE:
                parser.error(f'Input must be <= {MAX_MESSAGE} bytes per message')
        elif Path(args.file).exists():
            raise FileExistsError(f'Output already exists: {args.file}')
        with KcpLink(args.conv, args.rto_ms) as link, serial.Serial(
                args.port, args.baud, timeout=0, write_timeout=1, exclusive=True) as port:
            start = next_send = time.monotonic()
            delivered = False
            # A delimiter lets the peer resynchronize after a previous session.
            port.write(b'\x00')
            if payload is not None:
                link.send(payload)
            print(f'{args.mode}: conv={args.conv}, wire MTU=240, max {args.pps:g} frames/s',
                  file=sys.stderr, flush=True)
            while time.monotonic() - start < args.timeout:
                now = time.monotonic()
                link.feed(port.read(min(port.in_waiting, 4096)))
                for message in link.receive():
                    if args.mode == 'receive':
                        if delivered:
                            raise RuntimeError('Only one message allowed per CLI session')
                        with open(args.file, 'xb') as target:
                            target.write(message)
                        delivered = True
                        print(f'Reassembled {len(message)} bytes; continuing to ACK until timeout '
                              '(or Ctrl+C after sender completes)', file=sys.stderr, flush=True)
                # Keep reading while paced frames wait; do not accumulate retry bursts.
                if not link.outgoing:
                    link.update(int(now * 1000))
                if link.outgoing and now >= next_send:
                    frame = link.outgoing.popleft()
                    if port.write(frame) != len(frame):
                        raise OSError('Partial UART write')
                    next_send = time.monotonic() + 1 / args.pps
                if args.mode == 'send' and not link.pending and not link.outgoing:
                    print(f'Peer KCP acknowledged {len(payload)} bytes', file=sys.stderr)
                    return 0
                time.sleep(0.005)
            if args.mode == 'receive' and delivered:
                return 0
            raise TimeoutError('Session timeout; peer did not complete transfer')
    except KeyboardInterrupt:
        return 130
    except (ImportError, OSError, RuntimeError, ValueError, BufferError) as exc:
        print(f'Error: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
