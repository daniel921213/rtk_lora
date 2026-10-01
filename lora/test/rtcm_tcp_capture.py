#!/usr/bin/env python3
"""Capture raw TCP RTCM3 and report CRC-validated frame sizes (standard library only)."""

import argparse
import collections
import csv
from datetime import datetime
import json
import math
from pathlib import Path
import socket
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'extras' / 'rtcm_lora_link'))
from rtcm import RTCMStreamParser, extract_rtcm_message_type


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--host', default='192.168.10.11')
    cli.add_argument('--port', type=int, default=2101)
    cli.add_argument('--duration', type=float, default=15, help='Capture seconds after connection')
    cli.add_argument('--output-dir', type=Path, default=ROOT / 'runtime' / 'rtcm_capture',
                     help='Parent directory; each run creates a new timestamped subdirectory')
    args = cli.parse_args()
    if not math.isfinite(args.duration) or args.duration <= 0:
        cli.error('duration must be finite and positive')
    if not 1 <= args.port <= 65535:
        cli.error('port must be 1..65535')

    try:
        sock = socket.create_connection((args.host, args.port), timeout=5)
    except OSError as error:
        print(f'Connection failed: {error}', file=sys.stderr)
        return 1

    parser = RTCMStreamParser()
    raw_bins, valid_bins = collections.Counter(), collections.Counter()
    types = {}
    raw_total = valid_total = frames = empty_msm = 0
    reason = 'duration reached'
    failed = False
    output = args.output_dir.resolve() / datetime.now().strftime('%Y%m%d_%H%M%S_%f')
    with sock:
        output.mkdir(parents=True)
        print(f'TCP {args.host}:{args.port}; duration={args.duration:g}s\nOutput: {output}', flush=True)
        with (output / 'raw.rtcm3').open('wb') as raw, \
                (output / 'valid.rtcm3').open('wb') as valid, \
                (output / 'frames.csv').open('w', newline='') as details:
            writer = csv.writer(details)
            writer.writerow(['seconds', 'type', 'payload_bytes', 'frame_bytes',
                             'msm_satellites', 'msm_signals'])
            start = time.monotonic()
            try:
                while True:
                    remaining = args.duration - (time.monotonic() - start)
                    if remaining <= 0:
                        break
                    sock.settimeout(remaining)
                    try:
                        data = sock.recv(65536)
                    except socket.timeout:
                        break
                    if not data:
                        reason, failed = 'peer closed connection', True
                        break
                    elapsed = time.monotonic() - start
                    second = int(elapsed)
                    raw.write(data)
                    raw_total += len(data)
                    raw_bins[second] += len(data)
                    for frame in parser.feed(data):
                        valid.write(frame)
                        kind = extract_rtcm_message_type(frame)
                        size = len(frame)
                        valid_total += size
                        frames += 1
                        valid_bins[second] += size
                        stats = types.setdefault(str(kind), dict(count=0, min=size, max=size, total=0))
                        stats['count'] += 1
                        stats['total'] += size
                        stats['min'] = min(stats['min'], size)
                        stats['max'] = max(stats['max'], size)
                        nsat = nsig = ''
                        if kind is not None and 107 <= kind // 10 <= 113 and 1 <= kind % 10 <= 7 and size >= 28:
                            bits = ''.join(f'{byte:08b}' for byte in frame[3:-3])
                            nsat = bits[73:137].count('1')
                            nsig = bits[137:169].count('1')
                            if nsat == 0 or nsig == 0:
                                empty_msm += 1
                        writer.writerow([f'{elapsed:.6f}', kind, size - 6, size, nsat, nsig])
            except KeyboardInterrupt:
                reason = 'Ctrl+C'
            except OSError as error:
                reason, failed = f'socket error: {error}', True
            elapsed = max(time.monotonic() - start, 1e-9)

    for stats in types.values():
        stats['mean'] = stats['total'] / stats['count']
    per_second = []
    for second in range(math.ceil(elapsed)):
        window = min(1.0, elapsed - second)
        per_second.append(dict(second=second, window_seconds=window,
                               raw_bytes=raw_bins[second], valid_frame_bytes=valid_bins[second]))
    summary = dict(endpoint=f'{args.host}:{args.port}', duration=elapsed, stop_reason=reason,
                   raw_bytes=raw_total, valid_bytes=valid_total, frames=frames,
                   pending_bytes=parser.buffered_bytes(),
                   discarded_bytes=raw_total - valid_total - parser.buffered_bytes(),
                   raw_bytes_per_second=raw_total / elapsed,
                   valid_bytes_per_second=valid_total / elapsed,
                   empty_msm_frames=empty_msm, types=types, per_second=per_second)
    (output / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    print('\nSecond   Raw bytes   Valid frame bytes')
    for row in per_second:
        print(f"{row['second']:6d} {row['raw_bytes']:11d} {row['valid_frame_bytes']:19d}"
              + (' (partial second)' if row['window_seconds'] < .99 else ''))
    print('\nType    Count    Min B    Max B    Mean B')
    for kind, stats in sorted(types.items()):
        print(f"{kind:>4} {stats['count']:8d} {stats['min']:8d} {stats['max']:8d} {stats['mean']:9.1f}")
    print(f'\n{frames} valid frames, {valid_total} B / {elapsed:.2f}s = '
          f'{valid_total/elapsed:.1f} B/s ({valid_total*8/elapsed:.1f} bit/s)')
    print(f"Raw={raw_total} B; discarded={summary['discarded_bytes']} B; "
          f"pending={summary['pending_bytes']} B; stop={reason}")
    if empty_msm:
        print(f'Notice: {empty_msm} MSM frames have an empty satellite or signal mask; '
              'this does not represent a full observation load.')
    if not frames:
        print('No CRC-valid RTCM frames received.', file=sys.stderr)
    return 1 if failed or not frames else 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except OSError as error:
        print(f'Error: {error}', file=sys.stderr)
        sys.exit(1)
