"""Exercise real sender, receiver, tracker and str2str using pseudo serial ports."""
import csv
import os
from pathlib import Path
import pty
import re
import select
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest

from test_rtcm_transport import frame, record

ROOT = Path(__file__).resolve().parents[3]


def wait_for(fn, timeout=10):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        result = fn()
        if result:
            return result
        time.sleep(0.05)
    raise AssertionError('timed out waiting for pipeline')


@unittest.skipUnless(shutil.which('str2str'), 'str2str required for integration test')
class PipelineTests(unittest.TestCase):
    def test_binary_rtcm_to_gnss_and_gga_to_track(self):
        children = []
        fds = []
        stop = threading.Event()
        errors = []
        relay = None
        with tempfile.TemporaryDirectory() as tmp:
            logs = []
            def launch(script, *args):
                log = open(Path(tmp) / f'{len(children)}.log', 'w+')
                logs.append(log)
                child = subprocess.Popen([sys.executable, '-u', str(ROOT / script), *args],
                                         stdout=log, stderr=log, start_new_session=True)
                children.append(child)
                return child
            try:
                for _ in range(3):
                    fds.extend(pty.openpty())
                tx_master, tx_slave, rx_master, rx_slave, gnss_master, gnss_slave = fds
                with socket.socket() as source, socket.socket() as reserve:
                    source.bind(('127.0.0.1', 0)); source.listen(1); source.settimeout(10)
                    reserve.bind(('127.0.0.1', 0))
                    rx_port = reserve.getsockname()[1]
                    reserve.close()
                    receiver = launch('rover/rylr_reciver/receive_rtcm.py',
                        '--lora-port', os.ttyname(rx_slave), '--tcp-port', str(rx_port))
                    # Wait until the receiver opens the PTY and binds TCP.
                    def ready():
                        try:
                            c = socket.create_connection(('127.0.0.1', rx_port), timeout=.1)
                            c.close(); return True
                        except OSError:
                            return False
                    wait_for(ready)
                    track = str(Path(tmp) / 'track.csv')
                    tracker = launch('rover/rylr_reciver/tcp_gga_track.py',
                        '--tcp-port', str(rx_port), '--gnss-port', os.ttyname(gnss_slave), '--track', track)

                    def emulate_radio():
                        pending = bytearray()
                        try:
                            while not stop.is_set():
                                if not select.select([tx_master], [], [], .1)[0]:
                                    continue
                                pending.extend(os.read(tx_master, 4096))
                                while pending:
                                    if pending.startswith(b'AT\r\n'):
                                        del pending[:4]; os.write(tx_master, b'+OK\r\n'); continue
                                    match = re.match(rb'AT\+SEND=(\d+),(\d+),', pending)
                                    if not match:
                                        break
                                    size = int(match[2]); end = match.end()+size
                                    if len(pending) < end+2:
                                        break
                                    assert match[1] == b'67' and size <= 240
                                    assert pending[end:end+2] == b'\r\n'
                                    payload = bytes(pending[match.end():end]); del pending[:end+2]
                                    wire = record(payload)
                                    # Deliberately split metadata and binary payload at UART boundaries.
                                    for offset in range(0, len(wire), 17):
                                        os.write(rx_master, wire[offset:offset+17])
                                    os.write(tx_master, b'+OK\r\n')
                        except Exception as exc:
                            errors.append(exc)
                    relay = threading.Thread(target=emulate_radio, daemon=True); relay.start()
                    sender = launch('rtk_base/rylr_sender/send_rtcm.py',
                        '--tcp-host', '127.0.0.1', '--tcp-port', str(source.getsockname()[1]),
                        '--lora-port', os.ttyname(tx_slave))
                    conn, _ = source.accept()
                    with conn:
                        def tracker_connected():
                            logs[1].flush()
                            return '[tcp] connected' in Path(logs[1].name).read_text()
                        wait_for(tracker_connected)
                        original = frame(bytes(range(256))*3 + b',\r\n\x00+RCV=')
                        conn.sendall(original)
                        received = bytearray()
                        end = time.monotonic()+10
                        while len(received) < len(original) and time.monotonic() < end:
                            if select.select([gnss_master], [], [], .2)[0]:
                                received.extend(os.read(gnss_master, 4096))
                        self.assertEqual(bytes(received), original)
                        body = 'GNGGA,123456.00,2502.5384271,N,12132.1127602,E,4,20,0.8,20.0,M,15.2,M,1,0001'
                        checksum = 0
                        for c in body: checksum ^= ord(c)
                        gga = f'${body}*{checksum:02X}\r\n'.encode()
                        os.write(gnss_master, gga)
                        wait_for(lambda: Path(track).exists() and len(Path(track).read_text().splitlines()) >= 2)
                        with open(track) as f:
                            rows = list(csv.DictReader(f))
                        self.assertEqual(rows[0]['fix_quality'], '4')
                        self.assertEqual(rows[0]['raw_gga'], gga.decode().strip())
                        self.assertFalse(errors)
                        for child in (sender, receiver, tracker):
                            self.assertIsNone(child.poll())
            except Exception:
                for log in logs:
                    log.flush()
                    print(Path(log.name).read_text(), file=sys.stderr)
                raise
            finally:
                stop.set()
                for child in children:
                    if child.poll() is None:
                        os.killpg(child.pid, signal.SIGINT)
                        try: child.wait(timeout=4)
                        except subprocess.TimeoutExpired:
                            os.killpg(child.pid, signal.SIGKILL); child.wait()
                if relay: relay.join(timeout=2)
                for fd in fds: os.close(fd)
                for log in logs: log.close()


if __name__ == '__main__':
    unittest.main()
