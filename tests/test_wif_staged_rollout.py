"""Execute the WIF workflow's real deploy step against an offline cloud stub."""
import re
import textwrap
import test_deploy_debounce_admission as debounce_tests
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import yaml
from test_staged_rollout_full_step import GCLOUD
from test_staged_rollout_policy import evidence, IMAGE, MODULE

WORKFLOW = MODULE.parents[2] / '.github/workflows/reusable-deploy-cloud-run-wif.yml'

class WifStagedTests(unittest.TestCase):
    def execute(self, failure='', requested=False, existing=False, port=8080, staged=True):
        steps = yaml.safe_load(WORKFLOW.read_text())['jobs']['build-and-deploy']['steps']
        script = (next(s['run'] for s in steps if s.get('id') == 'staged_policy') + '\n') if staged else ''
        script += next(s['run'] for s in steps if s.get('name') == 'Deploy to Cloud Run')
        self.assertNotIn('${{', script)
        before = evidence()[0]
        before['spec'] = {'traffic': [{'latestRevision': True, 'percent': 100}],
            'template': {'metadata': {'name': 'service-old', 'annotations': {}},
                'spec': {'containers': [{'name': 'ingress', 'image': IMAGE, 'env': [],
                    'startupProbe': {'tcpSocket': {'port': 8080}}}]}}}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'state.json').write_text(json.dumps(before))
            # The WIF step passes --image as two argv entries; preserve that contract.
            stub = GCLOUD.replace('PYTHON', sys.executable, 1).replace(
                "args = sys.argv[1:]", "args = sys.argv[1:]\nif '--image' in args:\n    i = args.index('--image')\n    args[i:i+2] = ['--image=' + args[i+1]]")
            stub = stub.replace('httpGet.port=8080', 'httpGet.port=' + str(port)).replace("'port': 8080", "'port': " + str(port))
            if not staged:
                stub = '#!' + sys.executable + '\n' + "import json,os,pathlib,sys\np=pathlib.Path(os.environ['FIXTURE'])/'calls'\nwith p.open('a') as f:f.write(json.dumps(sys.argv[1:])+'\\n')\nassert sys.argv[1:3]==['run','deploy']\n"
            (root / 'gcloud').write_text(stub); (root / 'gcloud').chmod(0o755)
            env = {**os.environ, 'PATH': directory + os.pathsep + str(Path(sys.executable).parent) + os.pathsep + os.environ['PATH'],
                'FIXTURE': directory, 'FAILURE': failure, 'EXPECT_ALIAS': 'false',
                'EXISTING_PUBLIC': str(existing).lower(), 'STAGED_REQUESTED_PUBLIC': str(requested).lower(),
                'STAGED_HEALTH_PATH': '/healthz' if staged else '', 'STAGED_POLICY_FILE': str(root / 'policy.py'),
                'DEBOUNCE_ENVIRONMENT': '', 'IMAGE': IMAGE, 'DEPLOY_PROJECT_ID': 'project',
                'INPUT_SERVICE_NAME': 'service', 'INPUT_REGION': 'europe-west1',
                'INPUT_ENVIRONMENT': 'staging', 'INPUT_MIN_INSTANCES': '0', 'INPUT_MAX_INSTANCES': '10',
                'INPUT_TIMEOUT': '0', 'INPUT_PORT': str(port), 'INPUT_RUNTIME_SERVICE_ACCOUNT': '',
                'INPUT_INGRESS': '', 'INPUT_MEMORY': '', 'INPUT_CPU': '', 'INPUT_ENV_VARS': '',
                'INPUT_ALLOW_UNAUTHENTICATED': str(requested).lower(),
                'GITHUB_RUN_ID': '123', 'GITHUB_RUN_ATTEMPT': '1',
                'GITHUB_STEP_SUMMARY': str(root / 'summary')}
            result = subprocess.run(['bash', '-c', script], env=env, text=True, capture_output=True, timeout=15)
            calls = [json.loads(x) for x in (root / 'calls').read_text().splitlines()] if (root / 'calls').exists() else []
            return result, calls

    def test_exact_revision_promotes(self):
        result, calls = self.execute()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(sum(x[:3] == ['run', 'services', 'update-traffic'] for x in calls), 1)

    def test_custom_probe_port_is_verified(self):
        result, calls = self.execute(port=9090)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(any('--startup-probe=httpGet.path=/healthz,httpGet.port=9090,initialDelaySeconds=0,failureThreshold=24,timeoutSeconds=5,periodSeconds=10' in call for call in calls))

    def test_invalid_probe_port_never_promotes(self):
        result, calls = self.execute(port=65536)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any(x[:2] == ['run', 'deploy'] for x in calls))
        self.assertFalse(any(x[:3] == ['run', 'services', 'update-traffic'] for x in calls))

    def test_existing_public_access_preserved(self):
        result, _ = self.execute(requested=True, existing=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_access_mismatch_refuses_before_deploy(self):
        for requested, existing in [(True, False), (False, True)]:
            with self.subTest(requested=requested):
                result, calls = self.execute(requested=requested, existing=existing)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(any(x[:2] == ['run', 'deploy'] for x in calls))

    def test_read_failure_refuses_before_deploy(self):
        result, calls = self.execute(failure='iam-read')
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any(x[:2] == ['run', 'deploy'] for x in calls))

    def test_failed_health_or_deploy_never_promotes(self):
        for failure in ['health', 'deploy', 'foreign-no-traffic']:
            with self.subTest(failure=failure):
                result, calls = self.execute(failure=failure)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(any(x[:3] == ['run', 'services', 'update-traffic'] for x in calls))

    def test_debounce_is_native_and_disabled_by_default(self):
        doc = yaml.safe_load(WORKFLOW.read_text())
        inputs = doc.get('on', doc.get(True))['workflow_call']['inputs']
        self.assertEqual(inputs['debounce_environment']['default'], '')
        self.assertEqual(inputs['staged_health_path']['default'], '')
        self.assertFalse(doc['concurrency']['cancel-in-progress'])
        self.assertEqual(doc['jobs']['build-and-deploy']['needs'], 'debounce')
        self.assertIn('!cancelled()', doc['jobs']['build-and-deploy']['if'])
        self.assertIn('environment', doc['jobs']['debounce'])
        for job in doc['jobs'].values():
            for step in job.get('steps', []):
                self.assertLessEqual(len(step.get('run', '')), 21000)

    def test_legacy_deploy_preserves_access_flags_without_staging(self):
        for requested in (True, False):
            with self.subTest(requested=requested):
                result, calls = self.execute(staged=False, requested=requested)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(len(calls), 1)
                args = calls[0]
                self.assertIn('--allow-unauthenticated' if requested else '--no-allow-unauthenticated', args)
                self.assertNotIn('--no-traffic', args)
                self.assertNotIn('--deploy-health-check', args)
                self.assertEqual(args[args.index('--image') + 1], IMAGE)
                self.assertEqual(args[args.index('--project') + 1], 'project')


class WifDebounceTests(debounce_tests.DebounceAdmissionTests):
    @staticmethod
    def admission_block(name):
        match = re.search(r'^( *)# BEGIN ' + name + r'\n(.*?)^\1# END ' + name + r'$',
                          WORKFLOW.read_text(), re.M | re.S)
        if not match:
            raise AssertionError('missing WIF admission block')
        return textwrap.dedent(match.group(2))
