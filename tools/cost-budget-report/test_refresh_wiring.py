"""Execute only the added opt-in block with a fake cap; never the real digest."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).parent/'integration/refresh-v2'


class WiringTests(unittest.TestCase):
    def test_patch_binding_and_block_match(self):
        manifest=json.loads((ROOT/'digest-refresh-manifest.json').read_bytes())
        raw=(ROOT/'digest-refresh.patch').read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(),manifest['patch_sha256'])
        self.assertFalse(manifest['activation_authorized'])
        self.assertFalse(manifest['threshold_approved'])
        added=''.join(line[1:] for line in raw.decode().splitlines(True)
                      if line.startswith('+') and not line.startswith('+++'))
        self.assertIn((ROOT/'digest-refresh-block.sh').read_text(),added)

    def test_disabled_block_never_calls_cap(self):
        with tempfile.TemporaryDirectory() as directory:
            result=subprocess.run(['/bin/bash','-u',str(ROOT/'digest-refresh-block.sh')],
                env={'HOME':directory,'PATH':'/usr/bin:/bin','budget_root':directory},capture_output=True)
            self.assertEqual(result.returncode,0)
            self.assertEqual(result.stdout+result.stderr,b'')
            self.assertEqual(list(Path(directory).iterdir()),[])

    def test_enabled_block_wraps_entire_call_and_preserves_threshold(self):
        with tempfile.TemporaryDirectory() as directory:
            home=Path(directory)
            cap=home/'.merglbot/mem-guard/capped-run'
            cap.parent.mkdir(parents=True)
            # Only record argv; never execute the supplied child command.
            cap.write_text('#!/bin/bash\nprintf "%s\\n" "$@"\n')
            cap.chmod(0o700)
            result=subprocess.run(['/bin/bash','-u',str(ROOT/'digest-refresh-block.sh')],
                env={'HOME':directory,'PATH':'/usr/bin:/bin','budget_root':str(home/'budgets'),
                     'MERGLBOT_BUDGET_REFRESH_ENABLED':'1','MERGLBOT_BUDGET_REFRESH_HOURS':'12'},
                capture_output=True,text=True)
            self.assertEqual(result.returncode,0)
            self.assertEqual(result.stdout,'')
            argv=result.stderr.splitlines()
            self.assertEqual(argv[:7],['--max-gb','2','--timeout','150','--','/opt/homebrew/bin/python3','-I'])
            self.assertEqual(argv[-2:],['--refresh-after-hours','12'])
            self.assertIn(str(home/'.merglbot/gh-cost-wave3/budget-refresh-state/journal.jsonl'),argv)


if __name__ == '__main__': unittest.main()
