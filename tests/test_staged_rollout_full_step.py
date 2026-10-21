"""Run the complete deploy step, including secret reconciliation, entirely offline."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import yaml

from test_reusable_deploy_idempotent import BASH
from test_staged_rollout_policy import evidence, IMAGE
from test_staged_rollout_workflow import WORKFLOW

GCLOUD = r'''#!PYTHON
import copy, json, os, pathlib, sys, yaml
root = pathlib.Path(os.environ['FIXTURE'])
args = sys.argv[1:]
with (root / 'calls').open('a') as stream:
    stream.write(json.dumps(args) + '\n')
state = json.loads((root / 'state.json').read_text())
failure = os.environ.get('FAILURE', '')

def save():
    (root / 'state.json').write_text(json.dumps(state))

if args[:3] == ['run', 'services', 'describe']:
    if '--format=export' in args:
        if failure == 'foreign-before-alias':
            state['status']['traffic'] = [{'revisionName': 'service-foreign', 'percent': 100}]
            save()
        print(yaml.safe_dump(state))
        if failure in ('foreign-no-traffic', 'foreign-config-only'):
            if failure == 'foreign-no-traffic':
                state['status'].update(latestCreatedRevisionName='service-foreign',
                                      latestReadyRevisionName='service-foreign')
                state['spec']['template']['metadata']['name'] = 'service-foreign'
            state['metadata']['generation'] += 1
            state['status']['observedGeneration'] = state['metadata']['generation']
            state['spec']['template']['spec']['containers'][0]['env'].append(
                {'name': 'FOREIGN_NON_SECRET_CONFIG', 'value': 'preserve'})
            save()
    elif '--format=value(status.url)' in args:
        print('https://service.invalid')
    else:
        print(json.dumps(state))
elif args[:3] == ['run', 'services', 'get-iam-policy']:
    if failure == 'iam-read':
        sys.exit(7)
    print(json.dumps({'bindings': [{'role': 'roles/run.invoker', 'members': ['allUsers']}]}
                     if os.environ.get('EXISTING_PUBLIC') == 'true' else {}))
elif args[:2] == ['run', 'deploy']:
    assert '--allow-unauthenticated' not in args and '--no-allow-unauthenticated' not in args
    assert '--no-traffic' in args and '--deploy-health-check' in args
    assert '--startup-probe=httpGet.path=/healthz,httpGet.port=8080,initialDelaySeconds=0,failureThreshold=24,timeoutSeconds=5,periodSeconds=10' in args
    if os.environ.get('EXPECT_ALIAS') == 'true':
        assert '--update-secrets=FAKE_SECRET=projects/123/secrets/fixture:1' in args
        # Simulate a container whose health cannot succeed without its remote alias.
        state['spec']['template']['metadata']['annotations']['run.googleapis.com/secrets'] = 'sdk-alias:projects/123/secrets/fixture'
        state['spec']['template']['spec']['containers'][0]['env'] = [
            {'name': 'FAKE_SECRET', 'valueFrom': {'secretKeyRef': {'name': 'sdk-alias', 'key': '1'}}}]
    suffix = next(arg.split('=', 1)[1] for arg in args if arg.startswith('--revision-suffix='))
    image = next(arg.split('=', 1)[1] for arg in args if arg.startswith('--image='))
    state['spec']['template']['metadata']['name'] = 'service-' + suffix
    state['spec']['template']['spec']['containers'][0].update(
        image=image, startupProbe={'httpGet': {'path': '/healthz', 'port': 8080},
        'failureThreshold': 24, 'timeoutSeconds': 5, 'periodSeconds': 10})
    state['metadata']['generation'] += 1
    state['status'].update(observedGeneration=state['metadata']['generation'],
        latestReadyRevisionName='service-' + suffix, latestCreatedRevisionName='service-' + suffix)
    state['status']['conditions'][0]['status'] = 'True'
    if failure == 'alias-readback':
        state['spec']['template']['spec']['containers'][0]['env'] = []
    if failure in ('foreign-no-traffic', 'foreign-config-only', 'foreign-before-alias'):
        state['spec']['template']['spec']['containers'][0]['env'].append(
            {'name': 'FOREIGN_NON_SECRET_CONFIG', 'value': 'preserve'})
        state['status'].update(latestCreatedRevisionName='service-foreign',
                              latestReadyRevisionName='service-foreign')
        if failure == 'foreign-before-alias':
            state['status']['traffic'] = [{'revisionName': 'service-foreign', 'percent': 100}]
    save()
    if failure == 'deploy':
        sys.exit(4)
elif args[:3] == ['run', 'services', 'replace']:
    raise SystemExit('staged mode must not replace service YAML')
elif args[:3] == ['run', 'revisions', 'describe']:
    candidate = state['status']['latestCreatedRevisionName']
    if args[3] != candidate:
        sys.exit(3)
    print(json.dumps({
        'metadata': {'name': candidate, 'namespace': '123', 'generation': 1,
                     'labels': {'serving.knative.dev/service': 'service'}},
        'spec': state['spec']['template']['spec'],
        'status': {'observedGeneration': 1,
                   'imageDigest': state['spec']['template']['spec']['containers'][0]['image'],
                   'conditions': [{'type': 'Ready', 'status': 'True'},
                       {'type': 'ContainerHealthy', 'status': 'False' if failure == 'health' else 'True'}]}}))
elif args[:3] == ['run', 'services', 'update-traffic']:
    target = next(arg.split('=', 1)[1] for arg in args if arg.startswith('--to-revisions='))
    assert target == state['status']['latestCreatedRevisionName'] + '=100'
    state['status']['traffic'] = [{'revisionName': target.split('=')[0], 'percent': 100}]
    save()
else:
    raise SystemExit('unexpected gcloud call')
'''


@unittest.skipUnless(BASH, 'Bash >=4 required by existing mapfile usage')
class CompleteDeployStepTests(unittest.TestCase):
    def execute(self, *, cross_project=True, failure='', requested_public=False, existing_public=False):
        workflow = yaml.safe_load(WORKFLOW.read_text())
        step = next(step for step in workflow['jobs']['deploy']['steps'] if step.get('id') == 'deploy')
        prepare = next(s for s in workflow['jobs']['deploy']['steps'] if s.get('id') == 'staged_policy')
        script = prepare['run'] + '\n' + step['run']
        substitutions = {'steps.build.outputs.full_image': IMAGE, 'inputs.service_account': 'runner@project.invalid',
                         'inputs.allow_unauthenticated': 'false', 'inputs.env_vars': 'NODE_ENV=production'}
        for key, value in substitutions.items():
            script = script.replace('${{ ' + key + ' }}', value)
        self.assertNotIn('${{', script)
        before = evidence()[0]
        if failure == 'prior-failed':
            before['status']['conditions'][0]['status'] = 'False'
            before['status']['latestCreatedRevisionName'] = 'service-prior-failed'
        before['spec'] = {'traffic': [{'latestRevision': True, 'percent': 100}],
                          'template': {'metadata': {'name': 'service-old', 'annotations': {}},
                          'spec': {'containers': [{'name': 'ingress', 'image': IMAGE,
                                                   'env': [], 'startupProbe': {'tcpSocket': {'port': 8080}}}]}}}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for filename in ('service.yaml', 'inject_xproj_secret.py', 'service.json'):
                script = script.replace('/tmp/' + filename, str(root / filename))
            (root / 'state.json').write_text(json.dumps(before))
            (root / 'gcloud').write_text(GCLOUD.replace('PYTHON', sys.executable, 1))
            (root / 'gcloud').chmod(0o755)
            env = {**os.environ, 'PATH': directory + os.pathsep + str(Path(sys.executable).parent) + os.pathsep + os.environ['PATH'],
                   'FIXTURE': directory, 'STAGED_POLICY_FILE': str(root / 'policy-source.py'), 'FAILURE': failure, 'EXPECT_ALIAS': str(cross_project).lower(), 'STAGED_HEALTH_PATH': '/healthz',
                   'STAGED_IMAGE': IMAGE, 'DEBOUNCE_ENVIRONMENT': '',
                   'STAGED_REQUESTED_PUBLIC': str(requested_public).lower(), 'EXISTING_PUBLIC': str(existing_public).lower(),
                   'SERVICE': 'service', 'PROJECT_ID': 'project', 'REGION': 'europe-west1',
                   'GITHUB_RUN_ID': '123', 'GITHUB_RUN_ATTEMPT': '1',
                   'GITHUB_ENV': str(root / 'env'), 'GITHUB_OUTPUT': str(root / 'output'),
                   'GITHUB_STEP_SUMMARY': str(root / 'summary'),
                   'SECRETS_INPUT': 'FAKE_SECRET=projects/123/secrets/fixture:1' if cross_project else ''}
            result = subprocess.run([BASH, '-c', script], env=env, capture_output=True, text=True, timeout=15)
            calls = [json.loads(line) for line in (root / 'calls').read_text().splitlines()]
            final = json.loads((root / 'state.json').read_text())
            return result, calls, final

    def test_matching_public_access_is_preserved(self):
        result, calls, state = self.execute(requested_public=True, existing_public=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(any(c[:2] == ['run', 'deploy'] for c in calls))

    def test_access_mismatch_or_unavailable_policy_prevents_any_deploy(self):
        for requested, existing, failure in ((True, False, ''), (False, True, ''),
                                              (False, False, 'iam-read')):
            with self.subTest(requested=requested, existing=existing, failure=failure):
                result, calls, state = self.execute(requested_public=requested,
                                                   existing_public=existing, failure=failure)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(any(c[:2] == ['run', 'deploy'] or c[:3] ==
                                     ['run', 'services', 'update-traffic'] for c in calls))
                self.assertEqual(state['status']['traffic'][0]['revisionName'], 'service-old')

    def test_full_cross_project_step_promotes_only_final_verified_revision(self):
        result, calls, state = self.execute()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(state['status']['traffic'], [{'revisionName': 'service-r123-1-base', 'percent': 100}])
        kinds = [call[:3] for call in calls]
        self.assertNotIn(['run', 'services', 'replace'], kinds)
        self.assertEqual(sum(call[:2] == ['run', 'deploy'] for call in calls), 1)
        self.assertLess(kinds.index(['run', 'revisions', 'describe']), kinds.index(['run', 'services', 'update-traffic']))

    def test_repair_after_prior_failed_candidate_succeeds(self):
        result, calls, state = self.execute(failure='prior-failed')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(state['status']['traffic'][0]['revisionName'], 'service-r123-1-base')

    def test_no_cross_project_aliases_promotes_base_revision(self):
        result, calls, state = self.execute(cross_project=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(state['status']['traffic'], [{'revisionName': 'service-r123-1-base', 'percent': 100}])
        self.assertFalse(any(call[:3] == ['run', 'services', 'replace'] for call in calls))

    def test_reconcile_or_health_failure_leaves_old_traffic(self):
        for failure in ('deploy', 'alias-readback', 'health'):
            with self.subTest(failure=failure):
                result, calls, state = self.execute(failure=failure)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(state['status']['traffic'][0]['revisionName'], 'service-old')
                self.assertFalse(any(call[:3] == ['run', 'services', 'update-traffic'] for call in calls))

    def test_foreign_traffic_is_not_overwritten_by_alias_reconcile(self):
        result, calls, state = self.execute(failure='foreign-before-alias')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(state['status']['traffic'][0]['revisionName'], 'service-foreign')
        self.assertFalse(any(call[:3] == ['run', 'services', 'replace'] for call in calls))

    def test_foreign_nonserving_revision_or_config_is_not_overwritten(self):
        for failure in ('foreign-no-traffic', 'foreign-config-only'):
            with self.subTest(failure=failure):
                result, calls, state = self.execute(failure=failure)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(state['status']['traffic'][0]['revisionName'], 'service-old')
                self.assertIn({'name': 'FOREIGN_NON_SECRET_CONFIG', 'value': 'preserve'},
                              state['spec']['template']['spec']['containers'][0]['env'])
                self.assertFalse(any(call[:3] in (['run', 'services', 'replace'],
                                                 ['run', 'services', 'update-traffic']) for call in calls))


if __name__ == '__main__':
    unittest.main()
