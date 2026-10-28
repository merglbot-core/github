import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import hashlib

spec = importlib.util.spec_from_file_location('cost934_bundle', Path(__file__).with_name('adoption_bundle.py'))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class BundleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.directory = self.root / 'tools/session_context_install'
        self.directory.mkdir(parents=True)
        self.data = {'transaction': b'raise RuntimeError("must never execute during validation")',
                     'copy_admission': b'# synthetic second dependency'}
        for key, value in self.data.items():
            (self.directory / (key + '.py')).write_bytes(value)
        self.pins = {key: hashlib.sha256(value).hexdigest() for key, value in self.data.items()}

    def test_pure_read_validates_all_bytes_without_execution(self):
        with patch.object(m, 'PINS', self.pins):
            self.assertEqual(m.verified_sources(self.root), self.data)

    def test_single_changed_dependency_refuses_entire_bundle(self):
        (self.directory / 'copy_admission.py').write_bytes(b'changed')
        with patch.object(m, 'PINS', self.pins):
            with self.assertRaisesRegex(ValueError, 'pin_mismatch:copy_admission'):
                m.verified_sources(self.root)

    def test_missing_dependency_and_relative_path_refuse(self):
        with self.assertRaisesRegex(ValueError, 'absolute_bundle'):
            m.verified_sources('relative')
        (self.directory / 'transaction.py').unlink()
        with self.assertRaises(FileNotFoundError):
            m.verified_sources(self.root)

    def test_unisolated_loader_refuses_before_import(self):
        if m.sys.flags.isolated:
            self.skipTest('this runner is isolated')
        with self.assertRaisesRegex(ValueError, 'isolated_python_required'):
            m.load(self.root)
