"""Offline packet/rollback evidence tests; no production consumer is executed."""
import difflib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import install_adapter as adapter


class InstallAdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.source = self.root / 'source'
        (self.source / 'integration').mkdir(parents=True)
        self.targets = {key: self.root / key for key in adapter.TARGETS}
        contracts = {}
        for key in ('digest', 'monthly'):
            before = b'first\nlast\n'
            after = b'first\nadded\nlast\n'
            self.targets[key].write_bytes(before)
            delta = ''.join(difflib.unified_diff(before.decode().splitlines(True), after.decode().splitlines(True), fromfile='old', tofile='new')).encode()
            (self.source / f'integration/{key}.patch').write_bytes(delta)
            contracts[key] = {'baseline_sha256': adapter.digest(before),
                              'candidate_sha256': adapter.digest(after),
                              'patch_sha256': adapter.digest(delta)}
        (self.source / 'integration/manifest.json').write_text(json.dumps({'sources': contracts}))
        (self.source / 'budget_report.py').write_bytes(b'# inert renderer\n')
        self.packet = self.root / 'packet'

    def packet_ready(self):
        manifest, blobs = adapter.prepare(self.source, self.targets)
        self.packet.mkdir()
        for name, raw in blobs.items():
            (self.packet / name).write_bytes(raw)
        (self.packet / 'manifest.json').write_text(json.dumps(manifest))
        return manifest

    def verify(self):
        return adapter.verify_packet(self.packet, self.targets, self.source)

    def test_prepare_verify_does_not_install_and_has_rollback_first(self):
        before = {k: v.read_bytes() for k, v in self.targets.items() if v.exists()}
        m = self.packet_ready()
        self.assertTrue(self.verify()['preimages_match'])
        self.assertFalse(self.verify()['installation_authorized'])
        self.assertEqual(before, {k: v.read_bytes() for k, v in self.targets.items() if v.exists()})
        self.assertEqual(m['records']['renderer']['rollback_action'], 'remove_only_matching_postimage')
        self.assertEqual((self.packet / 'digest.backup').read_bytes(), before['digest'])
        self.assertFalse(self.targets['renderer'].exists())

    def test_existing_renderer_has_exact_backup(self):
        self.targets['renderer'].write_bytes(b'old renderer\n')
        self.packet_ready()
        self.assertEqual((self.packet / 'renderer.backup').read_bytes(), b'old renderer\n')
        self.assertTrue(self.verify()['preimages_match'])

    def test_changed_consumer_refused(self):
        self.targets['digest'].write_bytes(b'drift\n')
        with self.assertRaisesRegex(ValueError, 'reviewed contract'):
            adapter.prepare(self.source, self.targets)
        self.assertFalse(self.packet.exists())

    def test_metadata_drift_refused(self):
        self.packet_ready()
        self.targets['digest'].chmod(0o700)
        with self.assertRaises(ValueError):
            self.verify()

    def test_backup_tampering_refused(self):
        self.packet_ready()
        (self.packet / 'digest.backup').write_bytes(b'wrong\n')
        with self.assertRaisesRegex(ValueError, 'backup drift'):
            self.verify()

    def test_candidate_tampering_refused(self):
        self.packet_ready()
        (self.packet / 'renderer.candidate').write_bytes(b'wrong\n')
        with self.assertRaisesRegex(ValueError, 'candidate drift'):
            self.verify()

    def test_self_rebound_authorization_refused(self):
        m = self.packet_ready()
        m.pop('binding_sha256')
        m['installation_authorized'] = True
        m['binding_sha256'] = adapter.digest(json.dumps(m, sort_keys=True).encode())
        (self.packet / 'manifest.json').write_text(json.dumps(m))
        with self.assertRaises(ValueError):
            self.verify()

    def test_symlink_consumer_refused(self):
        self.targets['digest'].unlink()
        self.targets['digest'].symlink_to(self.targets['monthly'])
        with self.assertRaisesRegex(ValueError, 'symlink'):
            adapter.prepare(self.source, self.targets)

    def test_fifo_refused_without_waiting_for_writer(self):
        self.targets['digest'].unlink()
        os.mkfifo(self.targets['digest'])
        with self.assertRaisesRegex(ValueError, 'nonregular'):
            adapter.prepare(self.source, self.targets)

    def test_source_generation_drift_refused(self):
        self.packet_ready()
        (self.source / 'budget_report.py').write_bytes(b'new generation\n')
        with self.assertRaises(ValueError):
            self.verify()

    def test_new_renderer_between_preparation_and_verification_refused(self):
        self.packet_ready()
        self.targets['renderer'].write_bytes(b'foreign\n')
        with self.assertRaises(ValueError):
            self.verify()

    def test_xattr_drift_refused(self):
        self.packet_ready()
        with patch.object(adapter, 'xattrs', return_value={'synthetic': 'a' * 64}):
            with self.assertRaises(ValueError):
                self.verify()

    def test_real_reviewed_patches_reconstruct_pinned_candidates(self):
        source = Path(__file__).parent
        manifest = json.loads((source / 'integration/manifest.json').read_text())
        for key, contract in manifest['sources'].items():
            baseline = Path(contract['baseline_path'])
            if not baseline.is_file():
                self.skipTest('external historical baseline unavailable in isolated CI')
            candidate = adapter.patch_exact(baseline.read_bytes(), (source / f'integration/{key}.patch').read_bytes(), contract)
            self.assertEqual(adapter.digest(candidate), contract['candidate_sha256'])


if __name__ == '__main__':
    unittest.main()
