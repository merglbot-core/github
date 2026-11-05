"""Run only the added consumer blocks, never the original digest cleanup."""
from datetime import datetime, timezone
import datetime as dt
import hashlib
import html
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).parent


def added_block(name):
    lines = (ROOT / "integration" / (name + ".patch")).read_text().splitlines(True)
    return "".join(line[1:] for line in lines if line.startswith("+") and not line.startswith("+++"))


class ConsumerIntegrationTests(unittest.TestCase):
    def run_consumers(self, root, renderer=None):
        environment = {"MERGLBOT_BUDGET_ROOT": str(root),
                       "MERGLBOT_BUDGET_RENDERER": str(renderer or ROOT / "budget_report.py")}
        shell = subprocess.run(["/bin/bash", "-c", added_block("digest")],
                               env={**os.environ, **environment}, capture_output=True, text=True, check=True)
        paragraphs = []
        # Same escaping as the pinned monthly baseline's para/head backends.
        def para(value):
            paragraphs.append('<p>' + html.escape(value) + '</p>')
        with patch.dict(os.environ, environment):
            exec(compile(added_block("monthly"), "monthly-budget-integration", "exec"),
                 {"pathlib": __import__("pathlib"), "json": json, "dt": dt,
                  "head": paragraphs.append, "para": para})
        return shell.stdout, "\n".join(paragraphs)

    def test_same_snapshot_reaches_both_consumers_without_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            raw = json.dumps({"rows": [{"id": "synthetic", "budget_scope": "enterprise",
                  "product": "copilot", "budget_amount": 50, "consumed_amount": 3,
                  "prevent_further_usage": False, "alert_enabled": True}]}).encode()
            p = {"schema_version": 1, "complete": True,
                 "source_endpoint": "https://api.github.com/enterprises/example/settings/billing/budgets",
                 "source_sha256": hashlib.sha256(raw).hexdigest(),
                 "observed_at": datetime.now(timezone.utc).isoformat(), "period": None}
            (root / "latest.json").write_bytes(raw)
            (root / "latest.provenance.json").write_text(json.dumps(p))
            before = {f.name: f.read_bytes() for f in root.iterdir()}
            digest, monthly = self.run_consumers(root)
            expected = "| enterprise | copilot | 50 | 3 | ne | ano |"
            self.assertIn(expected, digest)
            self.assertIn('Scope: enterprise; Produkt: copilot; Budget USD: 50; API consumed USD: 3; Tvrdý stop: ne; Upozornění doloženo: ano', monthly)
            self.assertNotIn('|---', monthly)
            self.assertNotIn('`', monthly)
            for text in [digest, monthly]:
                self.assertIn(p["source_sha256"], text)
                self.assertIn("API období nedoloženo", text)
            self.assertEqual(before, {f.name: f.read_bytes() for f in root.iterdir()})

    def test_both_consumers_show_gaps_when_snapshot_or_renderer_missing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for renderer in [None, root / "missing-renderer.py"]:
                with self.subTest(renderer=renderer):
                    digest, monthly = self.run_consumers(root, renderer)
                    self.assertIn("DATA_GAP", digest)
                    self.assertIn("DATA_GAP", monthly)
                    self.assertEqual([], list(root.iterdir()))


if __name__ == "__main__":
    unittest.main()
