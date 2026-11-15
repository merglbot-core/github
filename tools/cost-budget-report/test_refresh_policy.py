import hashlib
import json
import unittest
from datetime import datetime, timedelta, timezone

from refresh_policy import decide


class RefreshPolicyTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 15, 19, tzinfo=timezone.utc)
        self.endpoint = 'https://api.github.com/enterprises/example/settings/billing/budgets'
        self.raw = b'{"rows":[]}'
        self.provenance = dict(schema_version=1, complete=True,
            source_endpoint=self.endpoint, source_sha256=hashlib.sha256(self.raw).hexdigest(),
            observed_at=self.now.isoformat(), period=None)

    def call(self, **kwargs):
        values = dict(enabled=True, raw=self.raw,
            provenance=json.dumps(self.provenance).encode(), attempt=None,
            now=self.now, endpoint=self.endpoint)
        values.update(kwargs)
        return decide(**values)

    def test_default_disabled_has_no_dependencies(self):
        self.assertEqual(self.call(enabled=False, raw=None, provenance=None), 'disabled')

    def test_fresh_unknown_period_preserved(self):
        before = dict(self.provenance)
        self.assertEqual(self.call(), 'reuse_existing_pair')
        self.assertEqual(self.provenance, before)

    def test_expiry_boundary(self):
        self.assertEqual(self.call(now=self.now + timedelta(hours=24)), 'reuse_existing_pair')
        self.assertEqual(self.call(now=self.now + timedelta(hours=24, seconds=1)), 'refresh_required')

    def test_future_observation_held(self):
        self.assertEqual(self.call(now=self.now - timedelta(seconds=1)), 'held_future_observation')

    def test_unresolved_attempt_never_replayed(self):
        for state in ('reserved', 'capturing', 'failed', 'publishing', 'unknown'):
            with self.subTest(state=state):
                self.assertEqual(self.call(attempt={'state': state}), 'held_unresolved_attempt')

    def test_published_attempt_binds_both_bytes(self):
        provenance = json.dumps(self.provenance).encode()
        attempt = dict(state='published_verified', snapshot_sha256=hashlib.sha256(self.raw).hexdigest(),
                       provenance_sha256=hashlib.sha256(provenance).hexdigest())
        self.assertEqual(self.call(attempt=attempt), 'reuse_existing_pair')
        self.assertEqual(self.call(attempt=attempt, raw=self.raw+b' '), 'held_published_pair_drift')
        self.assertEqual(self.call(attempt=attempt, provenance=provenance+b' '), 'held_published_pair_drift')

    def test_invalid_pair_not_reused(self):
        for change in ({'complete': False}, {'source_sha256': '0'*64},
                       {'source_endpoint': self.endpoint+'/foreign'}, {'schema_version': True}):
            with self.subTest(change=change):
                self.assertEqual(self.call(provenance=json.dumps(dict(self.provenance, **change)).encode()),
                                 'refresh_required')
        self.assertEqual(self.call(raw=None, provenance=None), 'refresh_required')
        self.assertEqual(self.call(provenance=b'[]'), 'refresh_required')

    def test_bad_clock_and_opt_in_refused(self):
        for change in ({'enabled': 'true'}, {'now': self.now.replace(tzinfo=None)},
                       {'max_age': timedelta(0)}, {'endpoint': 'https://foreign.invalid/budgets'}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.call(**change)


if __name__ == '__main__':
    unittest.main()
