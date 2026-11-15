from pathlib import Path
import tempfile
import unittest

from attempt_journal import locked_journal


class JournalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name).resolve()/'journal.jsonl'
        self.attempt = 'a'*32
        self.pins = dict(snapshot_sha256='b'*64, provenance_sha256='c'*64)

    def test_complete_chain_readback_and_next_attempt(self):
        with locked_journal(self.path) as journal:
            journal.append(self.attempt, 'reserved', {})
            for state in ('captured', 'staged', 'exchanged', 'verified'):
                journal.append(self.attempt, state, self.pins)
        before = self.path.read_bytes()
        with locked_journal(self.path) as journal:
            self.assertEqual(len(journal.records), 5)
            journal.append('d'*32, 'reserved', {})
        self.assertTrue(self.path.read_bytes().startswith(before))

    def test_interrupted_attempt_cannot_restart(self):
        with locked_journal(self.path) as journal:
            journal.append(self.attempt, 'reserved', {})
        before = self.path.read_bytes()
        with locked_journal(self.path) as journal:
            with self.assertRaises(ValueError): journal.append('d'*32, 'reserved', {})
        self.assertEqual(before, self.path.read_bytes())

    def test_invalid_transition_and_changed_binding(self):
        with locked_journal(self.path) as journal:
            journal.append(self.attempt, 'reserved', {})
            with self.assertRaises(ValueError): journal.append(self.attempt, 'verified', self.pins)
            journal.append(self.attempt, 'captured', self.pins)
            with self.assertRaises(ValueError): journal.append(self.attempt, 'staged', {})

    def test_partial_write_fails_closed(self):
        self.path.write_bytes(b'{')
        self.path.chmod(0o600)
        with self.assertRaises(ValueError):
            with locked_journal(self.path): pass
        self.assertEqual(self.path.read_bytes(), b'{')

    def test_concurrent_holder_refused(self):
        with locked_journal(self.path):
            with self.assertRaises(BlockingIOError):
                with locked_journal(self.path): pass


if __name__ == '__main__': unittest.main()
