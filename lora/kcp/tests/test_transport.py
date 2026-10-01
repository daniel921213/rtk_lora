import random
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from transport import (FrameDecoder, KcpLink, MAX_MESSAGE, cobs_decode,
                       cobs_encode, encode_frame)


class TransportTests(unittest.TestCase):
    def test_framing_boundaries(self):
        rng = random.Random(41)
        for size in range(24, 235):
            for packet in (bytes(size), b'\xff' * size, rng.randbytes(size)):
                frame = encode_frame(packet)
                self.assertLessEqual(len(frame), 240)
                parser = FrameDecoder()
                found = []
                for byte in frame:
                    found.extend(parser.feed(bytes([byte])))
                self.assertEqual(found, [packet])
        for size in (0, 254, 255, 512):
            raw = rng.randbytes(size)
            self.assertEqual(cobs_decode(cobs_encode(raw)), raw)

    def test_corruption_overflow_and_resync(self):
        packet = bytes(range(100))
        valid = encode_frame(packet)
        raw = bytearray(cobs_decode(valid[:-1]))
        raw[10] ^= 1
        parser = FrameDecoder()
        stream = (cobs_encode(raw) + b'\0' + b'\xff' * 10000 + b'\0'
                  + b'\xff\0' + valid + valid)
        self.assertEqual(parser.feed(stream), [packet, packet])
        self.assertEqual(parser.errors, 3)
        self.assertEqual(len(parser.buffer), 0)

    def test_loss_reordering_duplicates_and_clock_wrap(self):
        rng = random.Random(2026)
        for size in (0, 210, 211, 4096, MAX_MESSAGE):
            with self.subTest(size=size), KcpLink(123, 300) as a, KcpLink(123, 300) as b:
                payload = rng.randbytes(size)
                reply = b'return direction' * 100
                a.send(payload)
                b.send(reply)
                network = []
                received_a, received_b = [], []
                for now in range(0, 180000, 20):
                    for peer, target in ((a, b), (b, a)):
                        peer.update(0xfffff000 + now)
                        while peer.outgoing:
                            frame = peer.outgoing.popleft()
                            self.assertLessEqual(len(frame), 240)
                            if rng.random() < .15:
                                continue
                            if rng.random() < .05:
                                frame = frame[:5] + bytes([frame[5] ^ 0x40]) + frame[6:]
                            network.append((now + rng.randrange(0, 200), target, frame))
                            if rng.random() < .1:
                                network.append((now + rng.randrange(0, 200), target, frame))
                    rng.shuffle(network)
                    remaining = []
                    for due, target, frame in network:
                        if due <= now:
                            cut = rng.randrange(1, len(frame))
                            target.feed(frame[:cut])
                            target.feed(frame[cut:])
                        else:
                            remaining.append((due, target, frame))
                    network = remaining
                    received_a.extend(a.receive())
                    received_b.extend(b.receive())
                    if received_a and received_b and not a.pending and not b.pending and not network:
                        break
                self.assertEqual(received_b, [payload])
                self.assertEqual(received_a, [reply])
                self.assertEqual(a.pending, 0)
                self.assertEqual(b.pending, 0)

    def test_limits_session_and_closed_handle(self):
        with KcpLink(1) as a, KcpLink(2) as b:
            with self.assertRaises(ValueError):
                a.send(bytes(MAX_MESSAGE + 1))
            a.send(b'hello')
            with self.assertRaises(BufferError):
                a.send(b'another')
            a.update(0)
            a.update(100)
            while a.outgoing:
                b.feed(a.outgoing.popleft())
            self.assertEqual(b.receive(), [])
            self.assertGreater(b.decoder.errors, 0)
        a.close()
        with self.assertRaises(RuntimeError):
            a.update(0)


if __name__ == '__main__':
    unittest.main()
