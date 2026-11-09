import importlib.util
import tempfile
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('cost934_lock',
    Path(__file__).with_name('adoption_transaction.py'))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class LockTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name).resolve()

    def test_existing_lock_is_exclusive_and_never_removed(self):
        lock = self.base / 'lock'
        lock.write_bytes(b'')
        inode = lock.stat().st_ino
        with m.observer_lock(lock):
            with self.assertRaises(BlockingIOError):
                with m.observer_lock(lock):
                    self.fail('second lock admitted')
        self.assertEqual(lock.stat().st_ino, inode)
        with self.assertRaises(FileNotFoundError):
            with m.observer_lock(self.base / 'missing'):
                self.fail('created lock')

    def test_symlink_lock_refused(self):
        target = self.base / 'target'
        target.write_bytes(b'')
        link = self.base / 'link'
        link.symlink_to(target)
        with self.assertRaisesRegex(ValueError, 'symlink_refused'):
            with m.observer_lock(link):
                self.fail('symlink admitted')
