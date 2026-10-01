import hashlib
from pathlib import Path
import struct
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from file_transfer import FileSender, FileReceiver, MAGIC


class FileTests(unittest.TestCase):
    def test_examples_and_empty(self):
        examples = sorted((Path(__file__).resolve().parents[2] / 'example_files').glob('*'))
        self.assertTrue(examples)
        with tempfile.TemporaryDirectory() as directory:
            empty = Path(directory) / 'empty'
            empty.touch()
            for source in [empty] + examples:
                target = Path(directory) / (source.name + '.received')
                sender, receiver = FileSender(source), FileReceiver(target)
                try:
                    while (message := sender.next_message()) is not None:
                        reply = receiver.accept(message)
                        if reply:
                            sender.accept(reply)
                    self.assertTrue(sender.verified)
                    self.assertTrue(receiver.verified)
                    self.assertEqual(target.read_bytes(), source.read_bytes())
                finally:
                    sender.close()
                    receiver.close()

    def test_hash_failure_and_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'received'
            receiver = FileReceiver(target)
            receiver.accept(MAGIC + b'H' + struct.pack('<Q', 3))
            receiver.accept(MAGIC + b'Dabc')
            with self.assertRaises(ValueError):
                receiver.accept(MAGIC + b'E' + bytes(32))
            receiver.close()
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_no_overwrite_at_publication(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'received'
            receiver = FileReceiver(target)
            receiver.accept(MAGIC + b'H' + struct.pack('<Q', 0))
            target.write_bytes(b'existing')
            with self.assertRaises(FileExistsError):
                receiver.accept(MAGIC + b'E' + hashlib.sha256(b'').digest())
            receiver.close()
            self.assertEqual(target.read_bytes(), b'existing')
