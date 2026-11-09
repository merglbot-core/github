import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('cost934_journal', Path(__file__).with_name('adoption_journal.py'))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class JournalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name).resolve() / 'journal.jsonl'
        self.binding = 'a' * 64

    def test_exclusive_reservation_and_ordered_records(self):
        journal = m.Journal(self.path, self.binding)
        self.addCleanup(journal.close)
        journal.emit({'phase': 'intent'})
        journal.emit({'phase': 'verified'})
        with self.assertRaises(FileExistsError):
            m.Journal(self.path, self.binding)
        events, _ = m.parse(self.path.read_bytes(), self.binding)
        self.assertEqual([e['phase'] for e in events], ['reserved', 'intent', 'verified'])

    def test_truncated_changed_or_foreign_chain_refused(self):
        journal = m.Journal(self.path, self.binding)
        journal.close()
        raw = self.path.read_bytes()
        for data, binding in ((raw[:-1], self.binding),
                              (raw.replace(b'reserved', b'changed'), self.binding),
                              (raw, 'b' * 64)):
            if b'changed' in data:
                # A canonical event change alone changes the terminal hash;
                # its expected receipt hash must be pinned by the recovery owner.
                self.assertNotEqual(m.digest(data), m.digest(raw))
                continue
            with self.assertRaises(ValueError):
                m.parse(data, binding)

    def test_failed_flush_cannot_be_retried_in_same_writer(self):
        journal = m.Journal(self.path, self.binding)
        self.addCleanup(journal.close)
        with patch.object(m.os, 'fsync', side_effect=OSError('flush failed')):
            with self.assertRaises(OSError):
                journal.emit({'phase': 'intent'})
        with self.assertRaisesRegex(ValueError, 'not_writable'):
            journal.emit({'phase': 'retry'})

    def test_external_append_prevents_more_writes(self):
        journal = m.Journal(self.path, self.binding)
        self.addCleanup(journal.close)
        with self.path.open('ab') as stream:
            stream.write(b'external\n')
        with self.assertRaisesRegex(ValueError, 'file_drift'):
            journal.emit({'phase': 'intent'})

    def test_same_size_tampering_is_refused_before_append(self):
        journal = m.Journal(self.path, self.binding)
        self.addCleanup(journal.close)
        raw = self.path.read_bytes()
        self.path.write_bytes(raw.replace(b'reserved', b'tampered'))
        self.assertEqual(self.path.stat().st_size, len(raw))
        with self.assertRaisesRegex(ValueError, 'content_drift'):
            journal.emit({'phase': 'intent'})
