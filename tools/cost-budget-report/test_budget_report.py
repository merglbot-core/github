import copy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from budget_report import render


class BudgetReportTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 5, 12, tzinfo=timezone.utc)
        self.rows = [{"id": "synthetic", "budget_scope": "enterprise", "product": "actions",
                      "budget_amount": 100, "consumed_amount": 0, "prevent_further_usage": True}]
        self.provenance = {"schema_version": 1, "complete": True,
                           "source_endpoint": "https://api.github.com/enterprises/example/settings/billing/budgets",
                           "observed_at": "2026-11-05T11:00:00Z", "period": None}

    def output(self, rows=None, provenance=None):
        raw = json.dumps({"rows": self.rows if rows is None else rows}).encode()
        p = copy.deepcopy(self.provenance if provenance is None else provenance)
        p.setdefault("source_sha256", hashlib.sha256(raw).hexdigest())
        return render(raw, p, now=self.now)

    def test_zero_is_not_financial_acceptance(self):
        out = self.output()
        self.assertIn("| enterprise | actions | 100 | 0 | ano | DATA_GAP |", out)
        self.assertIn("Nulové consumed_amount neprokazuje nulové náklady", out)
        self.assertIn("API období nedoloženo", out)

    def test_prague_time_and_explicit_period(self):
        p = {**self.provenance, "period": {"start": "2026-10-31T23:00:00Z", "end": "2026-11-30T23:00:00Z"}}
        out = self.output(provenance=p)
        self.assertIn("2026-11-01T00:00:00+01:00", out)
        self.assertIn("2026-11-05T12:00:00+01:00", out)

    def test_soft_does_not_claim_alert_enabled(self):
        self.rows[0]["prevent_further_usage"] = False
        self.assertIn("| ne | DATA_GAP |", self.output())

    def test_alert_requires_boolean(self):
        self.rows[0]["alert_enabled"] = "true"
        self.assertIn("DATA_GAP", self.output())
        self.rows[0]["alert_enabled"] = True
        self.assertIn("| ano | ano |", self.output())

    def test_rejects_bad_provenance_without_numbers(self):
        for change in [{"source_sha256": "bad"}, {"complete": False}, {"observed_at": "2026-11-01T11:00:00Z"},
                       {"observed_at": "2026-11-06T11:00:00Z"}, {"observed_at": "2026-11-05T11:00:00"},
                       {"source_endpoint": "file:///tmp/input"}, {"schema_version": 2}]:
            with self.subTest(change=change):
                out = self.output(provenance={**self.provenance, **change})
                self.assertIn("DATA_GAP", out)
                self.assertNotIn("| actions |", out)

    def test_provenance_url_cannot_emit_query_credentials(self):
        for suffix in ["?token=synthetic", "#synthetic"]:
            out = self.output(provenance={**self.provenance, "source_endpoint": self.provenance["source_endpoint"] + suffix})
            self.assertNotIn("synthetic", out)
            self.assertIn("DATA_GAP", out)

    def test_invalid_timestamp_types_produce_gap_without_crashing(self):
        for value in [None, True, 123, [], {}]:
            for change in [{"observed_at": value},
                           {"period": {"start": value, "end": "2026-11-05T12:00:00Z"}},
                           {"period": {"start": "2026-11-05T11:00:00Z", "end": value}}]:
                with self.subTest(change=change):
                    out = self.output(provenance={**self.provenance, **change})
                    self.assertIn("DATA_GAP", out)
                    self.assertNotIn("| actions |", out)

    def test_cli_missing_and_invalid_inputs_are_explicit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            argv = [sys.executable, str(Path(__file__).with_name("budget_report.py")),
                    "--snapshot", str(root / "snapshot.json"), "--provenance", str(root / "provenance.json"),
                    "--max-age-hours", "24"]
            missing = subprocess.run(argv, capture_output=True, text=True, check=True)
            self.assertIn("DATA_GAP", missing.stdout)
            (root / "snapshot.json").write_text('{"rows": []}')
            (root / "provenance.json").write_text('{invalid')
            invalid = subprocess.run(argv, capture_output=True, text=True, check=True)
            self.assertIn("DATA_GAP", invalid.stdout)
            self.assertNotIn("Traceback", invalid.stderr)

    def test_cli_uses_provenance_without_refreshing_observation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            raw = json.dumps({"rows": self.rows}).encode()
            p = {**self.provenance, "observed_at": "2000-01-01T00:00:00Z",
                 "source_sha256": hashlib.sha256(raw).hexdigest()}
            (root / "snapshot.json").write_bytes(raw)
            (root / "provenance.json").write_text(json.dumps(p))
            argv = [sys.executable, str(Path(__file__).with_name("budget_report.py")),
                    "--snapshot", str(root / "snapshot.json"), "--provenance", str(root / "provenance.json"),
                    "--max-age-hours", "24"]
            result = subprocess.run(argv, capture_output=True, text=True, check=True)
            self.assertIn("DATA_GAP", result.stdout)
            self.assertNotIn("| actions |", result.stdout)
            self.assertEqual(raw, (root / "snapshot.json").read_bytes())
            self.assertEqual(p, json.loads((root / "provenance.json").read_text()))

    def test_rejects_duplicate_and_bad_amounts(self):
        self.assertNotIn("| actions |", self.output(rows=self.rows * 2))
        for value in [True, None, "NaN", "Infinity", -1]:
            row = {**self.rows[0], "consumed_amount": value}
            self.assertNotIn("| actions |", self.output(rows=[row]))

    def test_escapes_external_product(self):
        self.rows[0]["product"] = "<script>|\nvalue"
        out = self.output()
        self.assertNotIn("<script>", out)
        self.assertIn("&lt;script&gt;&#124; value", out)

    def test_exact_freshness_boundary(self):
        raw = json.dumps({"rows": self.rows}).encode()
        p = {**self.provenance, "source_sha256": hashlib.sha256(raw).hexdigest()}
        self.assertIn("| actions |", render(raw, p, now=self.now, max_age=timedelta(hours=1)))
        self.assertNotIn("| actions |", render(raw, p, now=self.now + timedelta(microseconds=1), max_age=timedelta(hours=1)))


if __name__ == "__main__":
    unittest.main()
