"""Protocol checks without LoRa hardware: python3 -m unittest discover -s lora_sender."""
import hashlib
from pathlib import Path
import tempfile
import unittest

from file_protocol import Link, Receiver, encode, encode_metadata, send_file


class BytesPort:
    def __init__(self, data=b''):
        self.data = bytearray(data)

    def read(self, size):
        part = bytes(self.data[:size])
        del self.data[:size]
        return part


class SimulatedLink:
    def __init__(self, receiver):
        self.receiver = receiver
        self.reply = None
        self.dropped = set()

    def send(self, sid, kind, seq, payload=b''):
        frame = encode(sid, kind, seq, payload)
        # Every real frame traverses the byte-stream decoder, including noise.
        packet = Link(BytesPort(b'noise\n' + frame)).receive(0.02)
        self.reply = self.receiver.handle(packet)
        # Lose one ACK at start, data and finish, forcing idempotent retries.
        if kind not in self.dropped:
            self.dropped.add(kind)
            self.reply = None

    def receive(self, timeout):
        reply, self.reply = self.reply, None
        return reply


class TransferTests(unittest.TestCase):
    def test_example_frames_fit_one_uart_write(self):
        meta = {'name': 'rosbag2_2026_02_25-12_40_24_0_rtk_trajectory.json',
                'size': 288450, 'wire_size': 54844, 'codec': 'zlib',
                'sha256': '00' * 32}
        for kind, payload in [(b'B', encode_metadata(meta)), (b'D', bytes(120))]:
            self.assertLessEqual(len(encode(b'12345678', kind, 0, payload)), 200)

    def test_example_roundtrip_with_lost_acks(self):
        example = Path(__file__).parent / 'example_files' / 'rosbag2_2026_02_25-12_40_24_0_rtk_trajectory.json'
        with tempfile.TemporaryDirectory() as folder:
            receiver = Receiver(folder)
            link = SimulatedLink(receiver)
            send_file(link, example, timeout=0.01, retries=2)
            self.assertEqual((Path(folder) / example.name).read_bytes(), example.read_bytes())
            self.assertEqual(link.dropped, {b'B', b'D', b'F'})

    def test_corrupt_and_truncated_frames_resynchronize(self):
        frame = encode(b'12345678', b'D', 1, b'hello')
        damaged = bytearray(frame)
        damaged[12] = ord('A') if damaged[12] != ord('A') else ord('B')
        port = BytesPort(bytes(damaged) + frame[:15] + frame)
        self.assertEqual(Link(port).receive(0.1), (b'12345678', b'D', 1, b'hello'))

    def test_reject_bad_hash_and_path_traversal(self):
        with tempfile.TemporaryDirectory() as folder:
            rx = Receiver(folder)
            meta = {'name': '../escape.json', 'size': 3, 'wire_size': 3,
                    'sha256': hashlib.sha256(b'abc').hexdigest(), 'codec': 'none'}
            self.assertEqual(rx.handle((b'12345678', b'B', 0, encode_metadata(meta)))[1], b'E')
            meta['name'] = 'test.json'
            self.assertEqual(rx.handle((b'12345678', b'B', 0, encode_metadata(meta)))[1], b'A')
            self.assertIsNone(rx.handle((b'12345678', b'D', 2, b'abc')))
            rx.handle((b'12345678', b'D', 1, b'bad'))
            self.assertEqual(rx.handle((b'12345678', b'F', 2, b''))[1], b'E')
            self.assertEqual(list(Path(folder).iterdir()), [])

    def test_empty_file_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as folder:
            src = Path(folder) / 'empty.json'
            src.write_bytes(b'')
            rx = Receiver(Path(folder) / 'out')
            send_file(SimulatedLink(rx), src, timeout=0.01, retries=2)
            with self.assertRaisesRegex(RuntimeError, 'already exists'):
                send_file(SimulatedLink(rx), src, timeout=0.01, retries=2)
            self.assertEqual((rx.output_dir / src.name).read_bytes(), b'')


if __name__ == '__main__':
    unittest.main()
