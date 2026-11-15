import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

from pair_exchange import exchange_existing
from pair_io import read_pair
from pair_stage import stage_pair


@unittest.skipUnless(sys.platform == 'darwin', 'native Darwin exchange required')
class ExchangeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.parent = Path(self.temp.name).resolve()

    def stage(self, raw):
        return stage_pair(self.parent, raw, json.dumps(dict(complete=True,
            source_sha256=hashlib.sha256(raw).hexdigest())).encode())

    def test_native_swap_retains_old_generation(self):
        old = self.stage(b'{"rows":[]}')
        new = self.stage(b'{"rows":[1]}')
        old_id, new_id = old.stat().st_ino, new.stat().st_ino
        fd = os.open(old, os.O_RDONLY | os.O_DIRECTORY)
        try:
            exchange_existing(old, new)
            self.assertEqual(old.stat().st_ino, new_id)
            self.assertEqual(new.stat().st_ino, old_id)
            self.assertEqual(read_pair(old)[0], b'{"rows":[1]}')
            self.assertEqual(read_pair(new)[0], b'{"rows":[]}')
            pinned = os.open('latest.json', os.O_RDONLY, dir_fd=fd)
            try: self.assertEqual(os.read(pinned, 100), b'{"rows":[]}')
            finally: os.close(pinned)
        finally: os.close(fd)

    def test_missing_destination_no_sequential_fallback(self):
        old = self.stage(b'{"rows":[]}')
        with self.assertRaises(FileNotFoundError):
            exchange_existing(old, self.parent/'missing')
        self.assertEqual(read_pair(old)[0], b'{"rows":[]}')

    def test_symlink_destination_refused(self):
        old = self.stage(b'{"rows":[]}')
        link = self.parent/'link'
        link.symlink_to(old, target_is_directory=True)
        with self.assertRaises(ValueError): exchange_existing(old, link)
        self.assertEqual(read_pair(old)[0], b'{"rows":[]}')


if __name__ == '__main__': unittest.main()
