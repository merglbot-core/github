import hashlib
import json
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

from pair_io import read_pair
import pair_stage


class StageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.parent = Path(self.temp.name).resolve()
        self.raw = b'{"rows": []}\n'
        self.provenance = json.dumps(dict(complete=True, period=None,
            observed_at='2026-11-15T17:54:57Z',
            source_sha256=hashlib.sha256(self.raw).hexdigest())).encode()

    def test_exact_bytes_private_and_not_published(self):
        staged = pair_stage.stage_pair(self.parent, self.raw, self.provenance)
        self.assertEqual(read_pair(staged), (self.raw, self.provenance))
        self.assertEqual(stat.S_IMODE(staged.stat().st_mode), 0o700)
        self.assertFalse((self.parent/'budgets').exists())
        for item in staged.iterdir():
            self.assertEqual(stat.S_IMODE(item.stat().st_mode), 0o600)

    def test_invalid_pair_creates_nothing(self):
        with self.assertRaises(ValueError):
            pair_stage.stage_pair(self.parent, self.raw+b' ', self.provenance)
        self.assertEqual(list(self.parent.iterdir()), [])

    def test_parent_symlink_refused(self):
        link = self.parent/'link'
        link.symlink_to(self.parent, target_is_directory=True)
        with self.assertRaises(ValueError):
            pair_stage.stage_pair(link, self.raw, self.provenance)

    def test_fsync_failure_preserves_partial_no_publication(self):
        with patch.object(pair_stage.os, 'fsync', side_effect=OSError('synthetic disk failure')):
            with self.assertRaises(OSError):
                pair_stage.stage_pair(self.parent, self.raw, self.provenance)
        self.assertFalse((self.parent/'budgets').exists())
        self.assertEqual(len(list(self.parent.iterdir())), 1)

    def test_separate_stages_do_not_overwrite(self):
        first = pair_stage.stage_pair(self.parent, self.raw, self.provenance)
        second = pair_stage.stage_pair(self.parent, self.raw, self.provenance)
        self.assertNotEqual(first, second)
        self.assertEqual(read_pair(first), read_pair(second))


if __name__ == '__main__': unittest.main()
