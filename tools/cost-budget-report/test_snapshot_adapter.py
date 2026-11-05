import copy
from datetime import datetime, timezone
import json
import unittest

from budget_report import render
from snapshot_adapter import normalize


class SnapshotAdapterTests(unittest.TestCase):
    def setUp(self):
        self.endpoint = "https://api.github.com/enterprises/example/settings/billing/budgets"
        self.budget = {"id": "synthetic", "budget_type": "ProductPricing", "budget_product_sku": "copilot",
                       "budget_scope": "enterprise", "budget_entity_name": "example", "budget_amount": 50,
                       "prevent_further_usage": False, "exclude_cost_center_usage": False,
                       "budget_alerting": {"will_alert": True, "alert_recipients": ["synthetic-recipient"]}}
        self.listing = [{"observed_at": "2026-11-05T11:00:00Z", "exit_code": 0,
                         "response": {"budgets": [self.budget], "total_count": 1, "has_next_page": False}}]
        self.details = [{"observed_at": "2026-11-05T11:01:00Z", "exit_code": 0,
                         "response": {**self.budget, "consumed_amount": 0}}]
        self.listing[0]['request_url'] = self.endpoint
        self.details[0]['request_url'] = self.endpoint + '/synthetic'

    def normalize(self, listing=None, details=None):
        return normalize(self.listing if listing is None else listing,
                         self.details if details is None else details, source_endpoint=self.endpoint)

    def test_captured_api_to_renderer_preserves_observation_and_removes_recipients(self):
        before = copy.deepcopy((self.listing, self.details))
        raw, provenance = self.normalize()
        text = render(raw, provenance, now=datetime(2026, 11, 5, 12, tzinfo=timezone.utc))
        self.assertIn("| enterprise | copilot | 50 | 0 | ne | ano |", text)
        self.assertEqual(provenance["observed_at"], "2026-11-05T11:00:00+00:00")
        self.assertIsNone(provenance["period"])
        self.assertNotIn("synthetic-recipient", raw.decode() + json.dumps(provenance))
        self.assertEqual(before, (self.listing, self.details))

    def test_refuses_missing_and_duplicate_detail(self):
        for details in [[], self.details * 2]:
            with self.assertRaises(ValueError):
                self.normalize(details=details)

    def test_refuses_partial_pages_and_changed_count(self):
        for field, value in [("has_next_page", True), ("total_count", 2)]:
            listing = copy.deepcopy(self.listing)
            listing[0]["response"][field] = value
            with self.assertRaises(ValueError):
                self.normalize(listing=listing)

    def test_refuses_changed_configuration_and_old_or_failed_read(self):
        for change in [{"observed_at": "2026-10-30T11:00:00Z"}, {"exit_code": 1}]:
            details = copy.deepcopy(self.details)
            details[0].update(change)
            with self.assertRaises(ValueError):
                self.normalize(details=details)
        details = copy.deepcopy(self.details)
        details[0]["response"]["prevent_further_usage"] = True
        with self.assertRaises(ValueError):
            self.normalize(details=details)

    def test_endpoint_and_page_binding(self):
        for target in ['listing', 'details']:
            for url in [self.endpoint.replace('/example/', '/other/'),
                        self.endpoint + '?page=2', self.endpoint + '/foreign',
                        self.endpoint + '#fragment', self.endpoint + '?token=synthetic']:
                listing, details = copy.deepcopy((self.listing, self.details))
                (listing if target == 'listing' else details)[0]['request_url'] = url
                with self.subTest(target=target, url=url), self.assertRaises(ValueError):
                    self.normalize(listing, details)
        listing = copy.deepcopy(self.listing)
        listing[0]['request_url'] += '?page=1&per_page=100'
        self.normalize(listing=listing)

    def test_malformed_captures_raise_value_error(self):
        for key in ['request_url', 'exit_code', 'response']:
            details = copy.deepcopy(self.details)
            del details[0][key]
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.normalize(details=details)
        details = copy.deepcopy(self.details)
        details[0]['response']['id'] = []
        with self.assertRaises(ValueError):
            self.normalize(details=details)


if __name__ == "__main__":
    unittest.main()
