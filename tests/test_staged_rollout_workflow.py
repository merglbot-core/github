"""Execute the hub's actual staging blocks against an offline gcloud stub."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import textwrap
import unittest
import yaml

from test_staged_rollout_policy import evidence, IMAGE, MODULE

WORKFLOW = MODULE.parents[2] / '.github/workflows/reusable-deploy-cloud-run.yml'


def block(name):
    match = re.search(rf'^( *)# BEGIN {name}\n(.*?)^\1# END {name}$',
                      WORKFLOW.read_text(), re.M | re.S)
    if not match:
        raise AssertionError(f'missing {name}')
    return textwrap.dedent(match.group(2))


STUB = '''#!{python}
import json, os, pathlib, sys
base = pathlib.Path(os.environ['FIXTURE'])
args = sys.argv[1:]
with (base / 'calls').open('a') as stream:
    stream.write(json.dumps(args) + '\\n')
if args[:3] == ['run', 'services', 'describe']:
    count = base / 'count'
    n = int(count.read_text()) if count.exists() else 0
    count.write_text(str(n + 1))
    print((base / ['before.json', 'current.json', 'after.json'][n]).read_text())
elif args[:3] == ['run', 'revisions', 'describe']:
    print((base / 'revision.json').read_text())
elif args[:3] == ['run', 'services', 'update-traffic']:
    if '--to-revisions=service-r123-1-base=100' not in args:
        sys.exit(9)
else:
    sys.exit(8)
'''


class WorkflowStagingTests(unittest.TestCase):
    def test_release_survives_optional_ancestor_skip_without_bypassing_guards(self):
        jobs = yaml.safe_load(WORKFLOW.read_text())['jobs']
        self.assertEqual(jobs['deploy']['needs'], 'debounce')
        self.assertEqual(jobs['publish_release_sbom']['needs'], 'deploy')
        # GitHub applies implicit success() without a status function, including
        # skipped ancestors. Both downstream jobs must opt out of that default.
        for name in ('deploy', 'publish_release_sbom'):
            self.assertIn('!cancelled()', jobs[name]['if'])
        release_guard = jobs['publish_release_sbom']['if']
        for required in ("needs.deploy.result == 'success'",
                         "startsWith(github.ref, 'refs/tags/')",
                         "github.repository_visibility != 'public'"):
            self.assertIn('&& ' + required, release_guard)
        self.assertNotIn('||', release_guard)

    def test_debounce_does_not_expand_existing_caller_permissions(self):
        jobs = yaml.safe_load(WORKFLOW.read_text())['jobs']
        # GitHub validates every nested job even when its runtime guard is false.
        # A caller satisfying the existing deploy contract must admit debounce.
        permissions = jobs['debounce']['permissions']
        self.assertEqual(permissions, {'contents': 'read', 'actions': 'read'})
        levels = {'none': 0, 'read': 1, 'write': 2}
        for scope, level in permissions.items():
            self.assertLessEqual(levels[level], levels[jobs['deploy']['permissions'].get(scope, 'none')])
        self.assertEqual(jobs['publish_release_sbom']['permissions']['actions'], 'read')

    def test_all_run_steps_fit_github_expression_length_limit(self):
        for job in yaml.safe_load(WORKFLOW.read_text())['jobs'].values():
            for step in job.get('steps', []):
                self.assertLessEqual(len(step.get('run', '')), 21000, step.get('name'))

    def test_embedded_helper_is_exact_hub_source(self):
        self.assertEqual(block('staged-policy-source'), MODULE.read_text())

    def test_prepare_and_promote_have_valid_bash_syntax(self):
        result = subprocess.run(['bash', '-n'], input=block('stage-prepare') + block('stage-promote'),
                                text=True, capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)

    def run_workflow(self, change=None):
        before, current, revision = evidence()
        name = 'service-r123-1-base'
        current['status'].update(latestCreatedRevisionName=name, latestReadyRevisionName=name)
        revision['metadata']['name'] = name
        after = json.loads(json.dumps(current))
        after['status']['traffic'] = [{'revisionName': name, 'percent': 100}]
        if change:
            change(current, revision)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for filename, doc in [('before', before), ('current', current), ('revision', revision), ('after', after)]:
                (root / (filename + '.json')).write_text(json.dumps(doc))
            stub = root / 'gcloud'
            stub.write_text(STUB.format(python=sys.executable))
            stub.chmod(0o755)
            env = {**os.environ, 'PATH': str(root) + os.pathsep + str(Path(sys.executable).parent) + os.pathsep + os.environ['PATH'],
                   'FIXTURE': directory, 'STAGED_POLICY_FILE': str(root / 'policy-source.py'), 'STAGED_HEALTH_PATH': '/healthz', 'STAGED_IMAGE': IMAGE,
                   'DEBOUNCE_ENVIRONMENT': '',
                   'GITHUB_RUN_ID': '123', 'GITHUB_RUN_ATTEMPT': '1', 'SERVICE': 'service',
                   'REGION': 'europe-west1', 'PROJECT_ID': 'project',
                   'GITHUB_STEP_SUMMARY': str(root / 'summary')}
            # Only the actual workflow blocks execute; gcloud cannot reach the network.
            script = 'set -euo pipefail\nSTAGE_DIR=""\nCROSS_PROJECT_SECRET_LINES=()\ntrap \'[ -z "$STAGE_DIR" ] || rm -rf "$STAGE_DIR"\' EXIT\n'
            helper_step = next(s for s in yaml.safe_load(WORKFLOW.read_text())['jobs']['deploy']['steps'] if s.get('id') == 'staged_policy')['run']
            result = subprocess.run(['bash', '-c', script + helper_step + '\n' + block('stage-prepare') + '\n' + block('stage-promote')],
                                    env=env, text=True, capture_output=True, timeout=10)
            calls = [json.loads(line) for line in (root / 'calls').read_text().splitlines()]
            summary = (root / 'summary').read_text() if (root / 'summary').exists() else ''
            return result, calls, summary

    def test_exact_revision_promotes_and_readbacks(self):
        result, calls, summary = self.run_workflow()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(sum(c[:3] == ['run', 'services', 'update-traffic'] for c in calls), 1)
        self.assertEqual(calls[-1][:3], ['run', 'services', 'describe'])
        self.assertIn('service-old=100', summary)

    def test_changed_traffic_never_promotes(self):
        def change(service, revision):
            service['status']['traffic'][0]['revisionName'] = 'service-foreign'
        result, calls, _ = self.run_workflow(change)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any(c[:3] == ['run', 'services', 'update-traffic'] for c in calls))

    def test_failed_probe_never_promotes(self):
        def change(service, revision):
            revision['status']['conditions'][1]['status'] = 'False'
        result, calls, _ = self.run_workflow(change)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any(c[:3] == ['run', 'services', 'update-traffic'] for c in calls))

    def test_mutating_deploy_is_staged_and_final_revision_is_pinned(self):
        source = WORKFLOW.read_text()
        self.assertIn('"--no-traffic" "--deploy-health-check"', source)
        self.assertIn('"--revision-suffix=${STAGE_SUFFIX}-base"', source)
        self.assertIn('ALL_SECRET_LINES=("${SAME_PROJECT_SECRET_LINES[@]}" "${CROSS_PROJECT_SECRET_LINES[@]}")', source)
        self.assertIn('&& [ -z "$STAGED_HEALTH_PATH" ]', source)
        self.assertLess(source.index('python3 "$STAGE_DIR/policy.py" --before'),
                        source.index('"${gcloud_cmd[@]}"'))
        self.assertNotIn('--to-latest', block('stage-promote'))


if __name__ == '__main__':
    unittest.main()
