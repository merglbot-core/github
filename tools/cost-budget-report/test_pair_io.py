import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import pair_io


class PairTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.parent = Path(self.temp.name).resolve()
        self.root = self.parent / 'budgets'
        self.make(self.root, b'{"rows":[]}')
        self.addCleanup(self.temp.cleanup)

    def make(self, root, raw):
        root.mkdir(mode=0o700)
        (root/'latest.json').write_bytes(raw)
        (root/'latest.provenance.json').write_text(json.dumps(dict(complete=True,
            source_sha256=hashlib.sha256(raw).hexdigest())))

    def test_reads_exact_bytes(self):
        self.assertEqual(pair_io.read_pair(self.root)[0], b'{"rows":[]}')

    def test_directory_descriptor_survives_path_replacement(self):
        original = os.open
        switched = False
        def opening(path, flags, *args, **kwargs):
            nonlocal switched
            if path == 'latest.provenance.json' and not switched:
                switched = True
                self.root.rename(self.parent/'archived')
                self.make(self.root, b'{"rows":[1]}')
            return original(path, flags, *args, **kwargs)
        with patch.object(pair_io.os, 'open', side_effect=opening):
            raw, provenance = pair_io.read_pair(self.root)
        self.assertEqual(raw, b'{"rows":[]}')
        self.assertEqual(json.loads(provenance)['source_sha256'], hashlib.sha256(raw).hexdigest())
        # This simulates pathname displacement, not a native atomic publisher test.
        self.assertEqual(pair_io.read_pair(self.root)[0], b'{"rows":[1]}')

    def test_mixed_pair_refused(self):
        (self.root/'latest.json').write_bytes(b'changed')
        with self.assertRaises(ValueError): pair_io.read_pair(self.root)

    def test_symlinks_refused(self):
        real = self.root/'actual'
        (self.root/'latest.json').rename(real)
        (self.root/'latest.json').symlink_to(real)
        with self.assertRaises(OSError): pair_io.read_pair(self.root)

    def test_fifo_refused_without_blocking(self):
        (self.root/'latest.json').unlink()
        os.mkfifo(self.root/'latest.json')
        with self.assertRaises(ValueError): pair_io.read_pair(self.root)

    def test_oversize_refused(self):
        with patch.object(pair_io, 'MAX_FILE_BYTES', 3):
            with self.assertRaises(ValueError): pair_io.read_pair(self.root)

    def test_writable_directory_refused(self):
        self.root.chmod(0o777)
        with self.assertRaises(ValueError): pair_io.read_pair(self.root)

    def test_hardlink_refused(self):
        os.link(self.root/'latest.json', self.parent/'extra')
        with self.assertRaises(ValueError): pair_io.read_pair(self.root)


if __name__ == '__main__': unittest.main()
