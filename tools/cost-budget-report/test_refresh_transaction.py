import copy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from attempt_journal import locked_journal
from pair_io import read_pair
from pair_stage import stage_pair
import refresh_transaction as tx


@unittest.skipUnless(sys.platform == 'darwin', 'native exchange required')
class TransactionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.parent = Path(self.temp.name).resolve()
        self.state = self.parent/'state'
        self.state.mkdir(mode=0o700)
        self.root = self.parent/'budgets'
        self.journal = self.state/'journal.jsonl'
        self.endpoint = 'https://api.github.com/enterprises/example/settings/billing/budgets'
        self.now = datetime(2026, 11, 15, 19, tzinfo=timezone.utc)
        raw = b'{"rows":[]}'
        provenance = json.dumps(dict(schema_version=1, complete=True, source_endpoint=self.endpoint,
            observed_at=(self.now-timedelta(days=2)).isoformat(), period=None,
            source_sha256=hashlib.sha256(raw).hexdigest())).encode()
        stage_pair(self.parent, raw, provenance).rename(self.root)
        self.old = read_pair(self.root)
        self.calls = []
        self.budget = dict(id='fixture', budget_type='ProductPricing', budget_product_sku='actions',
            budget_scope='enterprise', budget_amount=50, consumed_amount=2,
            prevent_further_usage=False, budget_alerting={'will_alert':True, 'alert_recipients':['private-fixture']})

    def get(self, url, *, timeout):
        self.calls.append(url)
        self.assertGreater(timeout, 0)
        if url.endswith('/rate_limit'): return {'resources':{'core':{'remaining':2200}}}
        if '?' in url: return {'budgets':[copy.deepcopy(self.budget)], 'total_count':1, 'has_next_page':False}
        return copy.deepcopy(self.budget)

    def run_refresh(self, **kwargs):
        args = dict(enabled=True, root=self.root, journal_path=self.journal, endpoint=self.endpoint,
                    refresh_after=timedelta(hours=12), get=self.get, clock=lambda:self.now)
        args.update(kwargs)
        return tx.refresh_once(**args)

    def test_full_fake_observation_retains_old_and_reuses_new(self):
        result = self.run_refresh()
        self.assertEqual(result['state'], 'published_verified')
        self.assertEqual(result['request_count'], 3)
        self.assertEqual(read_pair(Path(result['retained_prior_generation'])), self.old)
        raw, provenance = read_pair(self.root)
        self.assertNotIn(b'private-fixture', raw+provenance)
        self.assertIsNone(json.loads(provenance)['period'])
        self.assertEqual(self.run_refresh()['state'], 'reuse_existing_pair')
        self.assertEqual(len(self.calls), 3)

    def test_journal_inside_live_pair_refused_before_creation(self):
        with self.assertRaises(ValueError):
            self.run_refresh(journal_path=self.root/'journal.jsonl')
        self.assertEqual(set(p.name for p in self.root.iterdir()), {'latest.json','latest.provenance.json'})
        self.assertEqual(self.calls, [])

    def test_disabled_has_no_io(self):
        self.assertEqual(self.run_refresh(enabled=False)['state'], 'disabled')
        self.assertFalse(self.journal.exists())
        self.assertEqual(self.calls, [])

    def test_capture_failure_consumed_and_never_retried(self):
        with self.assertRaises(ValueError):
            self.run_refresh(get=lambda *a, **k: (_ for _ in ()).throw(ValueError('fixture transport failure')))
        self.assertEqual(self.run_refresh()['state'], 'held_unresolved_attempt')
        self.assertEqual(self.calls, [])
        self.assertEqual(read_pair(self.root), self.old)

    def test_shared_reserve_prevents_budget_reads(self):
        calls=[]
        def low(url, **kwargs):
            calls.append(url)
            return {'resources':{'core':{'remaining':2001}}}
        with self.assertRaises(ValueError): self.run_refresh(get=low)
        self.assertEqual(calls, ['https://api.github.com/rate_limit'])
        self.assertEqual(read_pair(self.root), self.old)

    def test_live_drift_before_exchange_is_held(self):
        original = tx.stage_pair
        def drift(*args, **kwargs):
            staged = original(*args, **kwargs)
            (self.root/'latest.json').write_bytes(b'foreign change')
            return staged
        with patch.object(tx, 'stage_pair', side_effect=drift), self.assertRaises(ValueError):
            self.run_refresh()
        with locked_journal(self.journal) as j:
            self.assertEqual(j.records[-1]['state'], 'staged')
        self.assertEqual(self.run_refresh()['state'], 'held_unresolved_attempt')

    def test_crash_after_exchange_does_not_replay_or_rollback(self):
        original = tx.exchange_existing
        def interrupted(*args):
            original(*args)
            raise RuntimeError('synthetic interruption after swap')
        with patch.object(tx, 'exchange_existing', side_effect=interrupted), self.assertRaises(RuntimeError):
            self.run_refresh()
        current=read_pair(self.root)
        self.assertNotEqual(current, self.old)
        self.assertEqual(self.run_refresh()['state'], 'held_unresolved_attempt')
        self.assertEqual(read_pair(self.root), current)
        self.assertEqual(len(self.calls), 3)

    def test_deadline_before_network_is_consumed(self):
        ticks=iter([0, 0, 121])
        with self.assertRaises(TimeoutError): self.run_refresh(ticks=lambda:next(ticks))
        self.assertEqual(self.calls, [])
        self.assertEqual(self.run_refresh()['state'], 'held_unresolved_attempt')


if __name__ == '__main__': unittest.main()
