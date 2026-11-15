import copy
from datetime import datetime, timezone
import json
import unittest
from unittest.mock import patch

from budget_reader import capture, gh_get


class BudgetReaderTests(unittest.TestCase):
    endpoint = 'https://api.github.com/enterprises/example/settings/billing/budgets'

    def setUp(self):
        self.budget = {'id': 'synthetic', 'budget_type': 'ProductPricing',
                       'budget_product_sku': 'actions', 'budget_scope': 'enterprise',
                       'budget_amount': 50, 'prevent_further_usage': False,
                       'budget_alerting': {'will_alert': True, 'alert_recipients': ['private-person']}}
        self.page = {'budgets': [self.budget], 'total_count': 1, 'has_next_page': False}
        self.calls = []

    def get(self, url):
        self.calls.append(url)
        return copy.deepcopy(self.page if '?' in url else {**self.budget, 'consumed_amount': 2})

    def run_capture(self, **kwargs):
        return capture(self.endpoint, get=kwargs.pop('get', self.get), remaining_requests=2100,
                       clock=lambda: datetime(2026, 11, 15, 12, tzinfo=timezone.utc), **kwargs)

    def test_complete_capture_preserves_urls_and_redacts_consumer_projection(self):
        raw, provenance, evidence = self.run_capture()
        self.assertEqual(evidence['requests_used'], 2)
        self.assertEqual(self.calls, [self.endpoint+'?page=1&per_page=100', self.endpoint+'/synthetic'])
        self.assertTrue(provenance['complete'])
        self.assertNotIn('private-person', raw.decode()+json.dumps(provenance))
        self.assertEqual(evidence['listing_reads'][0]['response'], self.page)

    def test_documented_plural_listing_single_detail(self):
        listing = copy.deepcopy(self.page)
        listing['budgets'][0].pop('budget_product_sku')
        listing['budgets'][0]['budget_product_skus'] = ['actions']
        raw, _, _ = self.run_capture(get=lambda url: listing if '?' in url else
                                    {**self.budget, 'consumed_amount': 2})
        self.assertEqual(json.loads(raw)['rows'][0]['product'], 'actions')

    def test_product_mismatch_refused(self):
        listing = copy.deepcopy(self.page)
        listing['budgets'][0]['budget_product_skus'] = ['packages']
        with self.assertRaises(ValueError):
            self.run_capture(get=lambda url: listing if '?' in url else
                             {**self.budget, 'consumed_amount': 2})

    def test_reserve_refuses_before_any_read(self):
        with self.assertRaises(ValueError):
            capture(self.endpoint, get=self.get, remaining_requests=2099)
        self.assertEqual(self.calls, [])

    def test_empty_complete_listing(self):
        self.page={'budgets': [], 'total_count': 0, 'has_next_page': False}
        raw, _, evidence=self.run_capture()
        self.assertEqual(json.loads(raw), {'rows': []})
        self.assertEqual(evidence['requests_used'],1)

    def test_incomplete_or_duplicate_pages_never_read_details(self):
        for update in ({'has_next_page':True}, {'total_count':2},
                       {'budgets':[self.budget,self.budget],'total_count':2}):
            with self.subTest(update=update), self.assertRaises(ValueError):
                self.run_capture(get=lambda url: {**self.page, **update})

    def test_detail_limit_is_checked_before_details(self):
        with self.assertRaises(ValueError): self.run_capture(max_requests=1)
        self.assertEqual(len(self.calls),1)

    def test_two_pages_are_captured_before_details(self):
        other = {**self.budget, 'id': 'second'}
        calls = []
        def get(url):
            calls.append(url)
            if '?page=1' in url:
                return {'budgets': [self.budget], 'total_count': 2, 'has_next_page': True}
            if '?page=2' in url:
                return {'budgets': [other], 'total_count': 2, 'has_next_page': False}
            return {**(other if url.endswith('/second') else self.budget), 'consumed_amount': 2}
        raw, _, evidence = self.run_capture(get=get)
        self.assertEqual(len(json.loads(raw)['rows']), 2)
        self.assertEqual(evidence['requests_used'], 4)
        self.assertIn('?page=2', calls[1])

    def test_identity_cannot_escape_detail_endpoint(self):
        for identity in ('.', '..', 'x/y', 'x?scope=user'):
            self.page['budgets'] = [{**self.budget, 'id': identity}]
            with self.subTest(identity=identity), self.assertRaises(ValueError):
                self.run_capture()

    def test_failed_get_is_not_retried(self):
        calls=[]
        def fail(url):
            calls.append(url)
            raise ValueError('unavailable')
        with self.assertRaises(ValueError): self.run_capture(get=fail)
        self.assertEqual(len(calls),1)

    def test_unknown_consumption_is_not_zero(self):
        with self.assertRaises(ValueError):
            self.run_capture(get=lambda url: self.page if '?' in url else self.budget)

    def test_foreign_endpoint_and_non_get_transport_are_impossible(self):
        for url in ('https://evil.example/budgets','https://api.github.com/user',self.endpoint+'?page=1&evil=2'):
            with self.subTest(url=url), patch('budget_reader.subprocess.run') as run:
                with self.assertRaises(ValueError): gh_get(url)
                run.assert_not_called()

    def test_transport_uses_explicit_get_and_existing_wrapper(self):
        with patch('budget_reader.subprocess.run') as run:
            run.return_value.returncode=0
            run.return_value.stdout=b'{}'
            gh_get(self.endpoint)
            args=run.call_args.args[0]
            self.assertEqual(args[args.index('--method')+1],'GET')
            self.assertIn('codex-guarded-command',args[1])
            self.assertNotIn('--input',args)

if __name__ == '__main__':
    unittest.main()
