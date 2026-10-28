from contextlib import contextmanager
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest

spec = importlib.util.spec_from_file_location('cost934_lifecycle', Path(__file__).with_name('adoption_lifecycle.py'))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.locked = False
        self.state = b'current attempts and STOP'
        self.events = []
        self.phases = []
        self.receipt = {'state': 'installed_verified'}
        self.change_state = False
        self.bootstrap_error = False

    def run_install(self):
        @contextmanager
        def lock():
            self.locked = True
            self.events.append('lock')
            try:
                yield
            finally:
                self.locked = False
                self.events.append('unlock')
        def bootstrap():
            self.assertTrue(self.locked)
            self.events.append('bootstrap')
        def completed():
            self.assertTrue(self.locked)
            self.events.append('completed')
            if self.change_state:
                self.state = b'new counter'
            if self.bootstrap_error:
                raise ValueError('no_proven_bootstrap_completion')
        def replace():
            self.assertTrue(self.locked)
            self.events.append('replace')
            return self.receipt
        service = SimpleNamespace(verify_old_idle=lambda: self.events.append('old_idle'),
            stop_and_verify_absent=lambda: self.events.append('stop'),
            verify_absent=lambda: self.events.append('absent'),
            bootstrap_new=bootstrap, verify_bootstrap_completed=completed)
        return m.install(service=service, lock=lock, guard=lambda: None,
            state_read=lambda: self.state, replace_files=replace,
            journal=lambda row: self.phases.append(row['phase']))

    def test_loaded_verification_precedes_unlock_and_is_not_financial_success(self):
        result = self.run_install()
        self.assertEqual(self.events, ['old_idle', 'stop', 'lock', 'absent', 'replace',
                                       'bootstrap', 'completed', 'unlock'])
        self.assertEqual(result['state'], 'installed_loaded_verified')
        self.assertEqual(result['financial_acceptance'], 'NOT_PROVEN')
        self.assertFalse(result['natural_run_verified'])

    def test_partial_replacement_never_bootstraps(self):
        self.receipt = {'state': 'partial_requires_live_readback'}
        result = self.run_install()
        self.assertNotIn('bootstrap', self.events)
        self.assertEqual(result['state'], 'partial_service_stopped')

    def test_unproven_bootstrap_stops_service_and_preserves_failure(self):
        self.bootstrap_error = True
        with self.assertRaisesRegex(ValueError, 'no_proven'):
            self.run_install()
        self.assertEqual(self.events[-2:], ['stop', 'unlock'])
        self.assertEqual(self.phases[-1], 'recovery_required_service_stopped')

    def test_new_counter_never_reverted(self):
        self.change_state = True
        with self.assertRaisesRegex(ValueError, 'state_changed_during_bootstrap'):
            self.run_install()
        self.assertEqual(self.state, b'new counter')
        self.assertEqual(self.events[-2:], ['stop', 'unlock'])


class RecoveryTests(unittest.TestCase):
    def run_recovery(self, partial=False, change_state=False):
        self.state = b'newest STOP attempts=3'
        self.events = []
        self.locked = False
        @contextmanager
        def lock():
            self.locked = True
            try:
                yield
            finally:
                self.locked = False
                self.events.append('unlock')
        def restored():
            self.assertTrue(self.locked)
            if change_state:
                self.state = b'even newer STOP attempts=4'
            return {'state': 'partial' if partial else 'rollback_verified'}
        def bootstrap():
            self.assertTrue(self.locked)
            self.events.append('bootstrap_previous')
        service = SimpleNamespace(stop_and_verify_absent=lambda: self.events.append('stop'),
            verify_absent=lambda: None, bootstrap_previous=bootstrap,
            verify_previous_bootstrap_completed=lambda: self.events.append('verified_previous'))
        return m.recover(service=service, lock=lock, guard=lambda: None,
            state_read=lambda: self.state, restore_files=restored, journal=lambda r: None)

    def test_recovery_bootstraps_restored_caller_under_lock(self):
        result = self.run_recovery()
        self.assertEqual(result['state'], 'rollback_loaded_verified')
        self.assertEqual(self.events, ['stop', 'bootstrap_previous', 'verified_previous', 'unlock'])
        self.assertEqual(self.state, b'newest STOP attempts=3')

    def test_partial_recovery_never_starts_service(self):
        result = self.run_recovery(partial=True)
        self.assertEqual(result['state'], 'partial_recovery_service_stopped')
        self.assertNotIn('bootstrap_previous', self.events)

    def test_new_state_during_recovery_is_preserved(self):
        with self.assertRaisesRegex(ValueError, 'state_changed_during_recovery'):
            self.run_recovery(change_state=True)
        self.assertEqual(self.state, b'even newer STOP attempts=4')
        self.assertEqual(self.events[-2:], ['stop', 'unlock'])
