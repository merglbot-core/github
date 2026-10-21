"""Run the WIF workflow's real deploy step with a recording gcloud stub (github#709).

Covers the opt-in env merge (env_update), the Secret Manager reference list (secret_env)
and the optional build-time NODE_AUTH_TOKEN secret.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / '.github/workflows/reusable-deploy-cloud-run-wif.yml'
IMAGE = 'europe-west1-docker.pkg.dev/p/r/service@sha256:' + 'a' * 64
RECORDER = (
    "import json,os,pathlib,sys\n"
    "p=pathlib.Path(os.environ['FIXTURE'])/'calls'\n"
    "with p.open('a') as f: f.write(json.dumps(sys.argv[1:])+'\\n')\n"
)


def steps():
    return yaml.safe_load(WORKFLOW.read_text())['jobs']['build-and-deploy']['steps']


def run_deploy(env_vars='', env_update='false', secret_env='', environment='staging'):
    script = next(s['run'] for s in steps() if s.get('name') == 'Deploy to Cloud Run')
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        (root / 'gcloud').write_text('#!' + sys.executable + '\n' + RECORDER)
        (root / 'gcloud').chmod(0o755)
        env = {**os.environ,
               'PATH': directory + os.pathsep + os.environ['PATH'],
               'FIXTURE': directory, 'STAGED_HEALTH_PATH': '', 'DEBOUNCE_ENVIRONMENT': '',
               'STAGED_POLICY_FILE': str(root / 'policy.py'), 'STAGED_REQUESTED_PUBLIC': 'false',
               'IMAGE': IMAGE, 'DEPLOY_PROJECT_ID': 'project',
               'INPUT_SERVICE_NAME': 'service', 'INPUT_REGION': 'europe-west1',
               'INPUT_ENVIRONMENT': environment, 'INPUT_MIN_INSTANCES': '0', 'INPUT_MAX_INSTANCES': '10',
               'INPUT_TIMEOUT': '0', 'INPUT_PORT': '8080', 'INPUT_RUNTIME_SERVICE_ACCOUNT': '',
               'INPUT_INGRESS': '', 'INPUT_MEMORY': '', 'INPUT_CPU': '',
               'INPUT_ALLOW_UNAUTHENTICATED': 'false',
               'INPUT_ENV_VARS': env_vars, 'INPUT_ENV_UPDATE': env_update, 'INPUT_SECRET_ENV': secret_env,
               'GITHUB_RUN_ID': '1', 'GITHUB_RUN_ATTEMPT': '1',
               'GITHUB_STEP_SUMMARY': str(root / 'summary')}
        result = subprocess.run(['bash', '-c', script], env=env, text=True, capture_output=True, timeout=15)
        calls_file = root / 'calls'
        calls = [json.loads(x) for x in calls_file.read_text().splitlines()] if calls_file.exists() else []
        return result, calls


def flag(args, prefix):
    return [a for a in args if a.startswith(prefix)]


class EnvModes(unittest.TestCase):
    def test_default_replaces_env_with_environment(self):
        result, calls = run_deploy(env_vars='A=1')
        self.assertEqual(result.returncode, 0, result.stderr)
        args = calls[0]
        self.assertEqual(flag(args, '--set-env-vars='), ['--set-env-vars=ENVIRONMENT=staging,A=1'])
        self.assertEqual(flag(args, '--update-env-vars'), [])
        self.assertEqual(flag(args, '--update-secrets'), [])

    def test_env_update_merges_without_environment(self):
        result, calls = run_deploy(env_vars='A=1,B=two', env_update='true', environment='production')
        self.assertEqual(result.returncode, 0, result.stderr)
        args = calls[0]
        self.assertEqual(flag(args, '--update-env-vars='), ['--update-env-vars=A=1,B=two'])
        self.assertEqual(flag(args, '--set-env-vars'), [])
        self.assertFalse(any('ENVIRONMENT=' in a for a in args))

    def test_env_update_without_vars_sends_no_env_flag(self):
        result, calls = run_deploy(env_update='true')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([a for a in calls[0] if 'env-vars' in a], [])

    def test_admin_shaped_values_pass_unchanged(self):
        value = ('NODE_ENV=production,AI_USAGE_SETTINGS_FIRESTORE_DATABASE_ID=(default),'
                 'CI_LOGIN_ALLOWED_JOB_WORKFLOW_REFS=o/r/.github/workflows/x.yml@refs/heads/main,'
                 'CI_LOGIN_ALLOWED_HOSTS=a.example;b-ew.a.run.app,BASE_URL=https://a.example')
        result, calls = run_deploy(env_vars=value, env_update='true')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(flag(calls[0], '--update-env-vars='), ['--update-env-vars=' + value])

    def test_environment_in_env_vars_is_still_refused(self):
        result, calls = run_deploy(env_vars='ENVIRONMENT=x', env_update='true')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(calls, [])


class SecretEnv(unittest.TestCase):
    def test_references_are_merged_with_default_version(self):
        listing = '\n'.join([
            '  GOOGLE_CLIENT_ID=google-client-id-secret:latest',
            '# a comment line',
            '',
            'SESSION_SECRET=projects/948953044532/secrets/session-secret-secret:latest',
            'PINNED=some-secret:7',
            'DEFAULTED=other-secret',
        ])
        result, calls = run_deploy(secret_env=listing)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(flag(calls[0], '--update-secrets='), [
            '--update-secrets=GOOGLE_CLIENT_ID=google-client-id-secret:latest,'
            'SESSION_SECRET=projects/948953044532/secrets/session-secret-secret:latest,'
            'PINNED=some-secret:7,DEFAULTED=other-secret:latest'])
        self.assertEqual(flag(calls[0], '--set-secrets'), [])

    def test_comments_only_send_no_secret_flag(self):
        result, calls = run_deploy(secret_env='# nothing\n\n')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(flag(calls[0], '--update-secrets'), [])

    def test_invalid_entries_refuse_before_deploy(self):
        for bad in ('no-equals-sign', '1BAD=secret', 'A=projects/name/secrets/x',
                    'A=bad/slash', 'A=secret:v1', 'A='):
            with self.subTest(bad=bad):
                result, calls = run_deploy(secret_env=bad)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(calls, [])


class BuildToken(unittest.TestCase):
    def test_token_is_a_buildkit_secret_only(self):
        build = next(s for s in steps() if s.get('id') == 'build')
        self.assertEqual(build['env']['NODE_AUTH_TOKEN'], '${{ secrets.NODE_AUTH_TOKEN }}')
        self.assertIn('--secret id=node_auth_token,env=NODE_AUTH_TOKEN', build['run'])
        self.assertNotIn('--build-arg', build['run'])
        call = yaml.safe_load(WORKFLOW.read_text())[True]['workflow_call']
        self.assertFalse(call['secrets']['NODE_AUTH_TOKEN']['required'])

    def test_new_inputs_default_to_previous_behaviour(self):
        inputs = yaml.safe_load(WORKFLOW.read_text())[True]['workflow_call']['inputs']
        self.assertIs(inputs['env_update']['default'], False)
        self.assertEqual(inputs['secret_env']['default'], '')


if __name__ == '__main__':
    unittest.main()
