"""Execute the workflow signing step with a fake gcloud; no cloud calls."""
import os
import json
from pathlib import Path
import re
import shlex
import subprocess
import sys
import tempfile
import textwrap
import unittest

WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/reusable-build-attest.yml"
INDEX = "sha256:" + "a" * 64
CHILD = "sha256:" + "b" * 64


class PlatformAttestationTests(unittest.TestCase):
    def run_step(self, child=CHILD, fail=False):
        source = WORKFLOW.read_text()
        step = source.split("      - name: Create Binary Authorization Attestation", 1)[1]
        script = textwrap.dedent(step.split("        run: |\n", 1)[1].split("\n  summary:", 1)[0])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stub = root / "gcloud"
            stub.write_text("""#!/bin/bash
printf '%s\\n' "$*" >> "$CALLS"
if [[ "$*" == *"$FAIL_DIGEST"* && -n "$FAIL_DIGEST" ]]; then exit 9; fi
printf 'projects/merglbot-artifacts/occurrences/test\\n'
""")
            stub.chmod(0o755)
            output = root / "output"
            calls = root / "calls"
            result = subprocess.run(["bash", "-c", script], capture_output=True, text=True,
                env={**os.environ, "PATH": str(root) + os.pathsep + os.environ["PATH"],
                     "IMAGE": "europe-west1-docker.pkg.dev/merglbot-artifacts/platform/test",
                     "INDEX_DIGEST": INDEX, "PLATFORM_DIGEST": child,
                     "FAIL_DIGEST": child if fail else "", "CALLS": str(calls),
                     "GITHUB_OUTPUT": str(output), "GITHUB_STEP_SUMMARY": str(root / "summary")})
            return result.returncode, output.read_text() if output.exists() else "", calls.read_text() if calls.exists() else ""

    def test_signs_index_and_verified_child_and_exports_both(self):
        code, output, calls = self.run_step()
        self.assertEqual(code, 0)
        self.assertEqual(calls.count("sign-and-create"), 2)
        self.assertIn(INDEX, calls)
        self.assertIn(CHILD, calls)
        self.assertIn("platform_attestation_id=projects/", output)
        self.assertIn("attestation_id=projects/", output)

    def test_child_failure_fails_job_without_list_fallback(self):
        code, output, calls = self.run_step(fail=True)
        self.assertEqual(code, 9)
        self.assertNotIn("platform_attestation_id=", output)
        self.assertNotIn("attestations list", calls)

    def test_missing_child_refuses_before_any_signing(self):
        code, _, calls = self.run_step(child="")
        self.assertNotEqual(code, 0)
        self.assertEqual(calls, "")

    def test_single_manifest_is_signed_once_but_exports_both(self):
        code, output, calls = self.run_step(child=INDEX)
        self.assertEqual(code, 0)
        self.assertEqual(calls.count("sign-and-create"), 1)
        self.assertIn("platform_attestation_id=projects/", output)

    def test_child_is_exported_from_successful_scan_parity(self):
        source = WORKFLOW.read_text()
        self.assertIn("platform_digest: ${{ steps.parity.outputs.platform_digest }}", source)
        self.assertIn("platform-digest: ${{ steps.parity.outputs.platform_digest }}", source)


class PlatformProvenanceTests(unittest.TestCase):
    def generate(self, candidate=INDEX, platform=CHILD):
        """Resolve workflow bindings, then execute the composite action's shell."""
        source = WORKFLOW.read_text()
        block = source.split('      - name: Generate SLSA Provenance\n', 1)[1].split('      - name: Upload SLSA Provenance\n', 1)[0]
        self.assertIn('uses: merglbot-core/github/actions/container-provenance@e6b0b8864f92b1453278250ae773d331c7a81165', block)
        self.assertNotIn('run:', block)
        values = {
            '${{ steps.build.outputs.digest }}': candidate,
            '${{ steps.parity.outputs.platform_digest }}': platform,
            '${{ inputs.registry }}': 'registry.example.invalid/fixtures',
            '${{ inputs.image_name }}': 'image',
            '${{ inputs.context }}': './services',
            '${{ inputs.dockerfile }}': './services/Dockerfile',
        }
        inputs = {name: values[value] for name, value in re.findall(r'^          ([a-z-]+): (.+)$', block, re.MULTILINE)}
        self.assertEqual(set(inputs), {'candidate-digest', 'platform-digest', 'registry', 'image-name', 'context', 'dockerfile'})
        action = WORKFLOW.parents[2] / 'actions/container-provenance'
        action_source = (action / 'action.yml').read_text()
        script = action_source.split('      run: ', 1)[1].strip()
        bindings = {}
        for name, value in re.findall(r'^        ([A-Z_]+): (.+)$', action_source, re.MULTILINE):
            if value == '${{ github.action_path }}':
                bindings[name] = str(action)
            else:
                match = re.fullmatch(r'\$\{\{ inputs\.([a-z-]+) \}\}', value)
                self.assertIsNotNone(match)
                bindings[name] = inputs[match[1]]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "output"
            tools = root / 'bin'
            tools.mkdir()
            python = tools / 'python3'
            python.write_text('#!/bin/sh\nexec ' + shlex.quote(sys.executable) + ' -B "$@"\n')
            python.chmod(0o755)
            env = {'PATH': str(tools) + os.pathsep + os.environ['PATH'],
                   'HOME': str(root), 'RUNNER_TEMP': str(root), 'GITHUB_OUTPUT': str(output),
                   'GITHUB_REPOSITORY': 'fixture/image', 'GITHUB_SHA': 'c' * 40,
                   'GITHUB_RUN_ID': '1234', 'GITHUB_RUN_ATTEMPT': '1',
                   'GITHUB_ACTOR': 'fixture-actor', 'GITHUB_REF': 'refs/heads/main',
                   'GITHUB_WORKFLOW': 'Offline workflow binding contract', **bindings}
            result = subprocess.run(['bash', '--noprofile', '--norc', '-eo', 'pipefail', '-c', script],
                                    cwd=root, env=env, capture_output=True, text=True, timeout=10)
            text = output.read_text() if output.exists() else ''
            documents = {}
            for name, path in re.findall(r'^(provenance_path|platform_provenance_path)=(.+)$', text, re.MULTILINE):
                artifact = Path(path)
                self.assertEqual(artifact.parent.parent, root)
                documents[name] = json.loads(artifact.read_text())
            return result.returncode, documents

    def test_projection_preserves_claims_and_selects_only_runtime(self):
        code, documents = self.generate()
        self.assertEqual(code, 0)
        full = documents['provenance_path']
        projected = documents['platform_provenance_path']
        self.assertEqual(projected['subject'], [{'name': 'registry.example.invalid/fixtures/image', 'digest': {'sha256': CHILD[7:]}}])
        self.assertEqual(projected['predicate'], full['predicate'])
        self.assertEqual(full['predicate']['buildConfig'], {'context': './services', 'dockerfile': './services/Dockerfile'})
        self.assertEqual(len(full['subject']), 2)

    def test_malformed_platform_fails_before_upload_outputs(self):
        code, documents = self.generate(platform='bad')
        self.assertNotEqual(code, 0)
        self.assertEqual(documents, {})

    def test_nonpush_has_no_platform_provenance(self):
        code, documents = self.generate(platform='')
        self.assertEqual(code, 0)
        self.assertEqual(set(documents), {'provenance_path'})

    def test_duplicate_plain_manifest_projects_one_subject(self):
        code, documents = self.generate(candidate=CHILD)
        self.assertEqual(code, 0)
        self.assertEqual(documents['platform_provenance_path']['subject'], [{'name': 'registry.example.invalid/fixtures/image', 'digest': {'sha256': CHILD[7:]}}])

    def test_pushing_build_without_candidate_fails_closed(self):
        code, documents = self.generate(candidate='')
        self.assertNotEqual(code, 0)
        self.assertEqual(documents, {})

    def test_nonpush_guard_and_upload_paths_stay_bound(self):
        source = WORKFLOW.read_text()
        block = source.split('      - name: Set up Python for provenance\n', 1)[1].split('\n  attest:\n', 1)[0]
        self.assertEqual(block.count("if: inputs.push || steps.build.outputs.digest != ''"), 3)
        self.assertIn("python-version: '3.11'", block)
        self.assertIn('actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97', block)
        upload = block.split('      - name: Upload SLSA Provenance\n', 1)[1]
        self.assertIn('${{ steps.provenance.outputs.provenance_path }}', upload)
        self.assertIn('${{ steps.provenance.outputs.platform_provenance_path }}', upload)
        self.assertNotIn('provenance.json', upload)
        self.assertNotIn('continue-on-error', block)
        self.assertNotIn('always()', block)


if __name__ == "__main__":
    unittest.main()
