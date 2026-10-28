import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest

spec = importlib.util.spec_from_file_location('cost934_native', Path(__file__).with_name('adoption_native.py'))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class NativeAdapterTests(unittest.TestCase):
    def setUp(self):
        self.rows = [{'target': p} for p in sorted(m.targets('/synthetic-owner'))]
        self.state = b'newest STOP and attempts'
        self.events = []

    def tx(self, change_state=False):
        def action(*args, invariant_check, journal):
            invariant_check()
            self.events.append('native_transaction')
            if change_state:
                self.state = b'newer STOP'
            invariant_check()
            result = {'state': 'partial_requires_live_readback'}
            journal(result)
            return result
        return SimpleNamespace(install=action, rollback=action,
                               rollback_preflight=lambda r: self.events.append('preflight'))

    def test_exact_pair_delegates_and_does_not_promote_partial(self):
        result = m.install(self.tx(), self.rows, '/synthetic-owner', '/backup',
            guard=lambda: None, state_read=lambda: self.state, journal=lambda r: None)
        self.assertEqual(result['state'], 'partial_requires_live_readback')

    def test_state_drift_is_refused_without_restoring_old_state(self):
        with self.assertRaisesRegex(ValueError, 'state_changed'):
            m.install(self.tx(True), self.rows, '/synthetic-owner', '/backup',
                guard=lambda: None, state_read=lambda: self.state, journal=lambda r: None)
        self.assertEqual(self.state, b'newer STOP')

    def test_foreign_state_target_is_not_delegated(self):
        self.rows[0]['target'] = '/synthetic-owner/.merglbot/gh-cost-wave3/acceptance_observer_state.json'
        with self.assertRaisesRegex(ValueError, 'exact_financial'):
            m.install(self.tx(), self.rows, '/synthetic-owner', '/backup',
                guard=lambda: None, state_read=lambda: self.state, journal=lambda r: None)
        self.assertEqual(self.events, [])

    def test_rollback_uses_current_state_and_native_preflight(self):
        m.rollback(self.tx(), {'rows': self.rows}, '/synthetic-owner',
            guard=lambda: None, state_read=lambda: self.state, journal=lambda r: None)
        self.assertEqual(self.events, ['preflight', 'native_transaction'])
        self.assertEqual(self.state, b'newest STOP and attempts')
