"""Inert-input and redaction contract of the reusable Cloud Run deploy step.

github-security-auditor#222: caller env_vars reach the shell only through env.
github-security-auditor#311: cross-project secret references never appear in
diagnostics or argv of the helper processes.
"""
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

import yaml

WORKFLOW = Path(__file__).resolve().parents[1] / '.github' / 'workflows' / 'reusable-deploy-cloud-run.yml'


def deploy_step():
    workflow = yaml.safe_load(WORKFLOW.read_text())
    return next(step for step in workflow['jobs']['deploy']['steps'] if step.get('id') == 'deploy')


def shell_function(script, name):
    """Return the body of a top-level shell function defined in the step script."""
    lines = script.splitlines()
    start = next(i for i, line in enumerate(lines) if line.strip() == name + '() {')
    indent = len(lines[start]) - len(lines[start].lstrip())
    end = next(i for i in range(start + 1, len(lines))
               if lines[i].strip() == '}' and len(lines[i]) - len(lines[i].lstrip()) == indent)
    return '\n'.join(lines[start:end + 1])


class InertInputTests(unittest.TestCase):
    def test_env_vars_are_passed_through_env(self):
        step = deploy_step()
        self.assertEqual(step['env']['ENV_VARS_INPUT'], '${{ inputs.env_vars }}')
        self.assertNotIn('inputs.env_vars', step['run'])

    def test_invalid_reference_diagnostic_does_not_echo_resource(self):
        script = deploy_step()['run']
        errors = [line for line in script.splitlines() if 'Invalid cross-project secret reference' in line]
        self.assertTrue(errors)
        for line in errors:
            self.assertNotIn('$secret_ref', line)
        self.assertLess(script.index('::add-mask::$secret_ref'), script.index('XPROJ_REF_INVALID'))

    def test_patcher_reads_values_from_environment(self):
        function = shell_function(deploy_step()['run'], 'write_xproj_patcher')
        self.assertNotIn('sys.argv', function)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            patcher = root / 'inject.py'
            service = root / 'service.yaml'
            body = function.replace('/tmp/inject_xproj_secret.py', str(patcher)).replace('/tmp/service.yaml', str(service))
            subprocess.run(['bash', '-c', 'set -euo pipefail\n' + body + '\nwrite_xproj_patcher'], check=True)
            service.write_text(yaml.safe_dump({'spec': {'template': {'metadata': {'annotations': {}},
                                                                     'spec': {'containers': [{'env': [{'name': 'KEEP', 'value': '1'}]}]}}}}))
            # Synthetic Secret Manager id, no real name and no value.
            resource = 'projects/' + '123' + '/secrets/' + 'fixture'
            env = {**os.environ, 'XPROJ_ENV_NAME': 'FIXTURE_REF', 'XPROJ_SECRET_RESOURCE': resource,
                   'XPROJ_SECRET_VERSION': '2'}
            subprocess.run([sys.executable, str(patcher)], env=env, check=True)
            svc = yaml.safe_load(service.read_text())
            template = svc['spec']['template']
            self.assertEqual(template['metadata']['annotations']['run.googleapis.com/secrets'], 'FIXTURE_REF:' + resource)
            self.assertEqual(template['spec']['containers'][0]['env'][-1],
                             {'name': 'FIXTURE_REF', 'valueFrom': {'secretKeyRef': {'name': 'FIXTURE_REF', 'key': '2'}}})

    def test_verifier_receives_entries_through_a_file(self):
        function = shell_function(deploy_step()['run'], 'verify_xproj_service')
        self.assertIsNone(re.search(r'python3 - "\$service_json" "\$@"', function))
        self.assertIn('entries_file=$(mktemp)', function)
        self.assertIn('rm -f -- "$entries_file"', function)


if __name__ == '__main__':
    unittest.main()
