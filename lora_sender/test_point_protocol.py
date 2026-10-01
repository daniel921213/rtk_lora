import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from point_protocol import Decoder, Receiver, encode, prepare, send_points

EXAMPLE = Path(__file__).parent / 'example_files' / 'rosbag2_2026_02_25-12_40_24_0_rtk_trajectory.json'


class LossyLink:
    def __init__(self, receiver):
        self.receiver = receiver
        self.drop = set()
        self.reply = None

    def send(self, *packet):
        decoded = Decoder().feed(encode(*packet))[0]
        reply = self.receiver.handle(decoded)
        if packet[1] not in self.drop:
            self.drop.add(packet[1])
            self.reply = None
        else:
            self.reply = Decoder().feed(encode(*reply))[0]

    def receive(self, timeout):
        reply, self.reply = self.reply, None
        return reply


class PointTests(unittest.TestCase):
    def test_example_roundtrip_lost_ack_each_type(self):
        packets = prepare(EXAMPLE, b'1234ABCD')
        self.assertEqual(len(packets), 774)
        self.assertTrue(all(len(encode(*p)) <= 58 for p in packets))
        self.assertEqual(sum(p[1] == b'S' for p in packets), 769)
        with tempfile.TemporaryDirectory() as folder, contextlib.redirect_stdout(io.StringIO()):
            receiver = Receiver(folder)
            send_points(LossyLink(receiver), packets, timeout=.01, retries=2)
            received = json.loads((Path(folder) / 'trajectory_1234ABCD.json').read_text())
            self.assertEqual(received, json.loads(EXAMPLE.read_text()))
            self.assertEqual(len(list(Path(folder).glob('*.json'))), 1)

    def test_stream_fragmentation_concatenation_noise(self):
        packets = prepare(EXAMPLE, b'1234ABCD')[:6]
        stream = b'noise' + b''.join(encode(*p) for p in packets)
        for size in (1, 3, 17, 58, 1024):
            decoder, out = Decoder(), []
            for i in range(0, len(stream), size):
                out.extend(decoder.feed(stream[i:i+size]))
            self.assertEqual(out, packets)

    def test_corruption_and_truncated_frame_resync(self):
        packets = prepare(EXAMPLE, b'1234ABCD')[:2]
        a, b = [encode(*p) for p in packets]
        for index in range(len(a)):
            corrupt = bytearray(a)
            corrupt[index] ^= 0x20
            self.assertEqual(Decoder().feed(bytes(corrupt) + b), [packets[1]])
        self.assertEqual(Decoder().feed(a[:8] + b), [packets[1]])

    def test_oversized_point_rejected_before_send(self):
        data = json.loads(EXAMPLE.read_text())
        data['samples'] = [{'x':1e20, 'y':1e20, 'z':1e20}]
        with tempfile.TemporaryDirectory() as folder:
            p = Path(folder) / 'large.json'
            p.write_text(json.dumps(data))
            with self.assertRaisesRegex(ValueError, 'payload exceeds'):
                prepare(p)

    def test_missing_point_and_wrong_checksum_no_file(self):
        packets = prepare(EXAMPLE, b'1234ABCD')
        with tempfile.TemporaryDirectory() as folder, contextlib.redirect_stdout(io.StringIO()):
            receiver = Receiver(folder)
            for packet in packets[:4]:
                receiver.handle(packet)
            self.assertEqual(receiver.handle(packets[-1])[1], b'X')
            self.assertEqual(list(Path(folder).iterdir()), [])
            receiver = Receiver(folder)
            for packet in packets[:-1]:
                receiver.handle(packet)
            sid, kind, seq, _ = packets[-1]
            self.assertEqual(receiver.handle((sid, kind, seq, b'769,00000000'))[1], b'X')
            self.assertEqual(list(Path(folder).iterdir()), [])

    def test_empty_points(self):
        with tempfile.TemporaryDirectory() as folder, contextlib.redirect_stdout(io.StringIO()):
            p = Path(folder) / 'empty.json'
            data = {'origin':{'latitude_deg':0,'longitude_deg':0,'altitude_m':0}, 'samples':[]}
            p.write_text(json.dumps(data))
            receiver = Receiver(Path(folder) / 'out')
            send_points(LossyLink(receiver), prepare(p,b'1234ABCD'),timeout=.01,retries=2)
            self.assertEqual(json.loads((receiver.output_dir/'trajectory_1234ABCD.json').read_text()),data)


if __name__ == '__main__':
    unittest.main()
