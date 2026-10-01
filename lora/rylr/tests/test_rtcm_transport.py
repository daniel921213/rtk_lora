import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rtcm_transport import RTCMParser, RYLRParser, crc24q, send_command, wait_ok


def frame(payload):
    header = b'\xd3' + len(payload).to_bytes(2, 'big')
    data = header + payload
    return data + crc24q(data).to_bytes(3, 'big')


def record(payload, address=69):
    return f'+RCV={address},{len(payload)},'.encode() + payload + b',-75,9\r\n'


class TransportTests(unittest.TestCase):
    def test_crc_known_vector(self):
        self.assertEqual(crc24q(b'123456789'), 0xCDE703)

    def test_wire_bytes_and_limits(self):
        data = b'\x00,\r\n\xff'
        self.assertEqual(send_command(67, data), b'AT+SEND=67,5,' + data + b'\r\n')
        for address, data in [(-1, b'x'), (65536, b'x'), (67, b''), (67, b'x'*241)]:
            with self.assertRaises(ValueError):
                send_command(address, data)

    def test_all_bytes_and_every_uart_split(self):
        payload = bytes(range(240))
        wire = b'+OK\r\n' + record(payload) + record(b'\xf0\xff\r\n,+RCV=')
        expected = [('line', b'+OK'), ('rcv', 69, payload, -75, 9),
                    ('rcv', 69, b'\xf0\xff\r\n,+RCV=', -75, 9)]
        for cut in range(len(wire)+1):
            parser = RYLRParser()
            self.assertEqual(parser.feed(wire[:cut]) + parser.feed(wire[cut:]), expected)
        parser = RYLRParser()
        events = []
        for value in wire:
            events.extend(parser.feed(bytes([value])))
        self.assertEqual(events, expected)

    def test_partial_record_expiry_and_recovery(self):
        parser = RYLRParser(timeout=1)
        self.assertEqual(parser.feed(b'+RCV=69,240,abc', now=1), [])
        self.assertEqual(parser.feed(b'', now=3)[0][0], 'malformed')
        self.assertEqual(parser.feed(record(b'good'), now=3)[0][2], b'good')

    def test_oversize_record_and_noise(self):
        parser = RYLRParser()
        events = parser.feed(b'noise+RCV=69,999,bad\r\n' + record(b'good'))
        self.assertTrue(any(e[0] == 'malformed' for e in events))
        self.assertEqual([e[2] for e in events if e[0]=='rcv'], [b'good'])

    def test_max_rtcm_frame_split_into_radio_packets(self):
        original = frame((bytes(range(256))*4)[:1023])
        parser = RTCMParser()
        recovered = []
        for offset in range(0, len(original), 240):
            wire = record(original[offset:offset+240])
            rcv = RYLRParser().feed(wire)[0]
            recovered.extend(parser.feed(rcv[2]))
        self.assertEqual(recovered, [original])

    def test_missing_fragment_and_crc_resync(self):
        original = frame(b'A'*600)
        good = frame(b'B'*100)
        parser = RTCMParser()
        # Omit a middle radio packet and follow with enough valid subsequent frames.
        recovered = parser.feed(original[:240]+original[480:]+good*4)
        self.assertEqual(recovered, [good]*4)
        self.assertGreater(parser.crc_errors, 0)

    def test_corrupt_frame_and_non_rtcm_noise(self):
        bad = bytearray(frame(b'abc'))
        bad[-1] ^= 1
        good = frame(b'good')
        parser = RTCMParser()
        self.assertEqual(parser.feed(b'$GNGGA,noise\r\n\xd3\xfc\x00'+bad+good), [good])
        self.assertGreater(parser.crc_errors, 0)

    def test_rtcm_timeout(self):
        parser = RTCMParser(timeout=1)
        parser.feed(frame(b'A'*400)[:200], now=1)
        good = frame(b'next')
        self.assertEqual(parser.feed(good, now=3), [good])

    def test_wait_ok_does_not_accept_substring(self):
        class Port:
            in_waiting = 0
            def __init__(self):
                self.parts = iter([record(b'+OK'), b'+ERR=17\r\n'])
            def read(self, size):
                return next(self.parts, b'')
        with self.assertRaises(RuntimeError):
            wait_ok(Port(), RYLRParser(), 0.1)


if __name__ == '__main__':
    unittest.main()
