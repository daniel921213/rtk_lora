from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from radio_io import RadioIO


class Port:
    def __init__(self):
        self.input = b''
        self.output = b''

    @property
    def in_waiting(self):
        return len(self.input)

    def read(self, count):
        data, self.input = self.input[:count], self.input[count:]
        return data

    def write(self, data):
        self.output += data
        return len(data)


class RadioTests(unittest.TestCase):
    def test_binary_receive_during_ok_wait(self):
        port = Port()
        radio = RadioIO(port, 'rylr', 69)
        payload = bytes(range(240))
        radio.send(payload, 0)
        self.assertEqual(port.output, b'AT+SEND=69,240,' + payload + b'\r\n')
        self.assertFalse(radio.ready)
        with self.assertRaises(RuntimeError):
            radio.send(payload, .1)
        port.input = b'+RCV=69,240,' + payload + b',-40,9\r\n+OK\r\n'
        self.assertEqual(radio.poll(.2), payload)
        self.assertTrue(radio.ready)

    def test_wrong_peer_errors_and_timeout(self):
        port = Port()
        radio = RadioIO(port, 'rylr', 69)
        port.input = b'+RCV=68,3,abc,-40,9\r\n'
        self.assertEqual(radio.poll(0), b'')
        radio.send(b'abc', 1)
        with self.assertRaises(TimeoutError):
            radio.poll(5)
        port.input = b'+ERR=1\r\n'
        with self.assertRaises(RuntimeError):
            radio.poll(6)
