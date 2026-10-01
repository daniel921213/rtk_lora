"""Exercise the actual CLI and pyserial using two bridged POSIX PTYs."""
import os
from pathlib import Path
import pty
import re
import select
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]


class CliTests(unittest.TestCase):
    def test_serial_transfer(self):
        self.run_transfer("link.py", bytes(range(256)) * 16, 6)

    def test_large_file_serial_transfer(self):
        source = next((ROOT.parent / "example_files").glob("*.png"))
        self.run_transfer("file_transfer.py", source.read_bytes(), 60, rylr=True)

    def run_transfer(self, script, payload, timeout, rylr=False):
        import serial  # Fail clearly when the documented dependency is missing.
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'input.bin'
            target = Path(directory) / 'output.bin'
            source.write_bytes(payload)
            master_a, slave_a = pty.openpty()
            master_b, slave_b = pty.openpty()
            processes = []
            try:
                common = [sys.executable, str(ROOT / script)]
                options = ['--conv', '94812', '--pps', '1000', '--timeout', str(timeout), '--rto-ms', '300']
                buffers = {master_a: bytearray(), master_b: bytearray()}
                rx_options = ['--radio', 'rylr', '--peer', '67'] if rylr else []
                tx_options = ['--radio', 'rylr', '--peer', '69'] if rylr else []
                rx = subprocess.Popen(common + ['receive', '--port', os.ttyname(slave_b),
                                      '--file', str(target)] + options + rx_options,
                                      stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                processes.append(rx)
                tx = subprocess.Popen(common + ['send', '--port', os.ttyname(slave_a),
                                      '--file', str(source)] + options + tx_options,
                                      stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                processes.append(tx)
                deadline = time.monotonic() + timeout + 5
                while time.monotonic() < deadline and any(p.poll() is None for p in processes):
                    ready, _, _ = select.select([master_a, master_b], [], [], .02)
                    for fd in ready:
                        data = os.read(fd, 4096)
                        other = master_b if fd == master_a else master_a
                        if rylr:
                            buffers[fd].extend(data)
                            data = bytearray()
                            while buffers[fd]:
                                match = re.match(rb'AT\+SEND=(\d+),(\d+),', buffers[fd])
                                if not match:
                                    break
                                size = int(match[2])
                                self.assertLessEqual(size, 240)
                                end = match.end() + size
                                if len(buffers[fd]) < end + 2:
                                    break
                                self.assertEqual(buffers[fd][end:end+2], b'\r\n')
                                frame = bytes(buffers[fd][match.end():end])
                                del buffers[fd][:end+2]
                                address = 67 if fd == master_a else 69
                                data.extend(f'+RCV={address},{size},'.encode() + frame + b',-40,10\r\n')
                                os.write(fd, b'+OK\r\n')
                        # Serial read boundaries may split a single frame anywhere.
                        for offset in range(0, len(data), 17):
                            os.write(other, data[offset:offset + 17])
                for process in processes:
                    self.assertIsNotNone(process.poll(), 'CLI did not terminate')
                    _, stderr = process.communicate(timeout=1)
                    self.assertEqual(process.returncode, 0, stderr.decode())
                self.assertEqual(target.read_bytes(), source.read_bytes())
            finally:
                for process in processes:
                    if process.poll() is None:
                        process.kill()
                    process.communicate()
                for fd in (master_a, slave_a, master_b, slave_b):
                    os.close(fd)
