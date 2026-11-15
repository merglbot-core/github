from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import budget_report
from pair_stage import stage_pair


class CoherentConsumerTests(unittest.TestCase):
    def test_renderer_root_and_monthly_function_use_same_pair(self):
        with tempfile.TemporaryDirectory() as directory:
            raw=json.dumps({'rows':[dict(id='synthetic',budget_scope='enterprise',product='copilot',
                budget_amount=50,consumed_amount=2,prevent_further_usage=False,alert_enabled=True)]}).encode()
            p=dict(schema_version=1,complete=True,source_sha256=hashlib.sha256(raw).hexdigest(),
                   source_endpoint='https://api.github.com/enterprises/example/settings/billing/budgets',
                   observed_at=datetime.now(timezone.utc).isoformat(),period=None)
            root=stage_pair(Path(directory).resolve(),raw,json.dumps(p).encode())
            before={f.name:f.read_bytes() for f in root.iterdir()}
            result=subprocess.run([sys.executable,'-B',str(Path(budget_report.__file__)),
                '--root',str(root),'--max-age-hours','24'],capture_output=True,text=True,check=True)
            current, provenance=budget_report.read_current(root)
            monthly=budget_report.render_paragraphs(current,provenance,now=datetime.now(timezone.utc))
            self.assertIn('| enterprise | copilot | 50 | 2 | ne | ano |',result.stdout)
            self.assertIn(p['source_sha256'],str(monthly))
            self.assertEqual(before,{f.name:f.read_bytes() for f in root.iterdir()})

    def test_cli_rejects_ambiguous_source(self):
        result=subprocess.run([sys.executable,'-B',str(Path(budget_report.__file__)),
            '--root','/not-used','--snapshot','/not-used','--max-age-hours','24'],capture_output=True)
        self.assertEqual(result.returncode,2)


if __name__ == '__main__': unittest.main()
