"""Execute the real native-timer admission block; no network or runner sleep."""
import datetime
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from test_staged_rollout_workflow import block

SHA = 'a' * 40
GH = '''#!{python}
import os, pathlib, sys
root = pathlib.Path(os.environ['FIXTURE'])
endpoint = sys.argv[2]
if '/environments/' in endpoint:
    print((root / 'environment.json').read_text())
elif endpoint.endswith('/actions/runs/123'):
    print((root / 'own.json').read_text())
elif endpoint.endswith('/actions/runs/99'):
    print((root / 'ci.json').read_text())
elif endpoint.endswith('/git/ref/heads/main'):
    print((root / 'head').read_text())
else:
    sys.exit(7)
'''


class DebounceAdmissionTests(unittest.TestCase):
    def execute(self, *, timer=10, age=650, head=SHA, ci_status='completed',
                ci_conclusion='success', repository='org/repo', event='workflow_run'):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run = {'id': 99, 'name': 'ci', 'event': 'push', 'head_branch': 'main', 'head_sha': SHA,
                   'conclusion': 'success', 'status': 'completed', 'head_repository': {'full_name': repository}}
            (root / 'event.json').write_text(json.dumps({'workflow_run': run}))
            run.update(status=ci_status, conclusion=ci_conclusion)
            (root / 'ci.json').write_text(json.dumps(run))
            created = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=age)
            (root / 'own.json').write_text(json.dumps({'head_sha': SHA, 'event': event,
                                                      'created_at': created.isoformat()}))
            (root / 'environment.json').write_text(json.dumps({'protection_rules': [
                {'type': 'wait_timer', 'wait_timer': timer}]}))
            (root / 'head').write_text(head)
            (root / 'gh').write_text(GH.format(python=sys.executable))
            (root / 'gh').chmod(0o755)
            env = {**os.environ, 'PATH': directory + os.pathsep + str(Path(sys.executable).parent) + os.pathsep + os.environ['PATH'],
                   'FIXTURE': directory, 'RUNNER_TEMP': directory, 'GITHUB_REPOSITORY': 'org/repo',
                   'GITHUB_SHA': SHA, 'GITHUB_RUN_ID': '123', 'GITHUB_EVENT_NAME': event,
                   'GITHUB_EVENT_PATH': str(root / 'event.json'), 'GITHUB_OUTPUT': str(root / 'output'),
                   'DEBOUNCE_ENVIRONMENT': 'deploy-debounce'}
            result = subprocess.run(['bash', '-c', 'set -euo pipefail\n' + block('deploy-debounce-admission')],
                                    env=env, capture_output=True, text=True, timeout=10)
            return result, (root / 'output').read_text().splitlines()[-1]

    def test_native_wait_and_live_green_head_admitted(self):
        result, verdict = self.execute()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(verdict, 'eligible=true')

    def test_stale_head_skips_without_deployment_success(self):
        result, verdict = self.execute(head='b' * 40)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(verdict, 'eligible=false')

    def test_bypassed_or_shortened_wait_refused(self):
        for options in ({'age': 599}, {'age': -20}, {'timer': 0}, {'timer': 5}, {'timer': 11}):
            with self.subTest(options=options):
                result, verdict = self.execute(**options)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(verdict, 'eligible=false')

    def test_restarted_or_failed_ci_refused(self):
        for options in ({'ci_status': 'in_progress'}, {'ci_conclusion': 'failure'},
                        {'ci_conclusion': 'cancelled'}):
            with self.subTest(options=options):
                result, verdict = self.execute(**options)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(verdict, 'eligible=false')

    def test_foreign_or_manual_event_not_inferred_green(self):
        for options in ({'repository': 'foreign/repo'}, {'event': 'workflow_dispatch'}):
            result, verdict = self.execute(**options)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(verdict, 'eligible=false')

    def test_no_paid_sleep_in_admission(self):
        self.assertNotRegex(block('deploy-debounce-admission'), r'(?m)^\s*sleep\s')


if __name__ == '__main__':
    unittest.main()
