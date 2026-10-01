"""Transparent UART and RYLR AT adapters; neither changes radio settings."""
from pathlib import Path
import sys

try:
    from ..rylr.rtcm_transport import RYLRParser, send_command
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from lora.rylr.rtcm_transport import RYLRParser, send_command


class RadioIO:
    def __init__(self, port, mode='transparent', peer=None, ok_timeout=3):
        if mode not in ('transparent', 'rylr'):
            raise ValueError('Unknown radio mode')
        if mode == 'rylr' and (peer is None or not 0 <= peer <= 65535):
            raise ValueError('RYLR requires peer address 0..65535')
        self.port, self.mode, self.peer = port, mode, peer
        self.parser = RYLRParser()
        self.waiting_since = None
        self.ok_timeout = ok_timeout
        self.tx_bytes = self.rx_bytes = self.errors = 0

    @property
    def ready(self):
        return self.waiting_since is None

    def poll(self, now):
        raw = self.port.read(min(self.port.in_waiting, 4096))
        self.rx_bytes += len(raw)
        if self.mode == 'transparent':
            return raw
        data = bytearray()
        for event in self.parser.feed(raw, now):
            if event[0] == 'rcv':
                if event[1] == self.peer:
                    data.extend(event[2])
            elif event[0] == 'line':
                if event[1] == b'+OK':
                    self.waiting_since = None
                elif event[1].startswith(b'+ERR'):
                    raise RuntimeError(f'RYLR rejected command: {event[1]!r}')
            elif event[0] == 'malformed':
                self.errors += 1
        if self.waiting_since is not None and now - self.waiting_since > self.ok_timeout:
            raise TimeoutError('RYLR +OK timeout')
        return bytes(data)

    def send(self, frame, now):
        if not self.ready:
            raise RuntimeError('Previous RYLR command is still pending')
        command = send_command(self.peer, frame) if self.mode == 'rylr' else frame
        if self.port.write(command) != len(command):
            raise OSError('Partial UART write')
        self.tx_bytes += len(command)
        if self.mode == 'rylr':
            self.waiting_since = now
