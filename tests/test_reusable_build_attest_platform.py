"""Execute the workflow signing step with a fake gcloud; no cloud calls."""
import os
import json
from pathlib import Path
import subprocess
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
        self.assertIn("PLATFORM_DIGEST: ${{ steps.parity.outputs.platform_digest }}", source)
        self.assertIn(".subject += [{name: .subject[0].name, digest: {sha256: $digest}}]", source)


class PlatformProvenanceTests(unittest.TestCase):
    def project(self, subjects, digest=CHILD):
        source = WORKFLOW.read_text()
        block = source.split('          # Keep the full statement artifact', 1)[1]
        script = textwrap.dedent(block[block.index('          if ['):].split('      - name: Upload SLSA Provenance', 1)[0])
        document = {"subject": subjects, "predicate": {"materials": [{"digest": {"sha1": "c" * 40}}]}}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "output"
            result = subprocess.run(["bash", "-e", "-c", script], cwd=root, capture_output=True, text=True,
                env={**os.environ, "PLATFORM_DIGEST": digest, "PROVENANCE": json.dumps(document), "GITHUB_OUTPUT": str(output)})
            artifact = root / "platform-provenance.json"
            return result.returncode, json.loads(artifact.read_text()) if artifact.exists() else None, document

    def test_projection_preserves_claims_and_selects_only_runtime(self):
        subjects = [{"name": "image", "digest": {"sha256": INDEX}}, {"name": "image", "digest": {"sha256": CHILD[7:]}}]
        code, projected, original = self.project(subjects)
        self.assertEqual(code, 0)
        self.assertEqual(projected["subject"], [subjects[1]])
        self.assertEqual(projected["predicate"], original["predicate"])
        self.assertEqual(len(original["subject"]), 2)

    def test_missing_platform_subject_fails(self):
        code, projected, _ = self.project([{"name": "image", "digest": {"sha256": INDEX}}])
        self.assertNotEqual(code, 0)
        self.assertIsNone(projected)

    def test_nonpush_has_no_platform_provenance(self):
        code, projected, _ = self.project([], digest="")
        self.assertEqual(code, 0)
        self.assertIsNone(projected)

    def test_duplicate_plain_manifest_projects_one_subject(self):
        subject = {"name": "image", "digest": {"sha256": CHILD}}
        code, projected, _ = self.project([subject, subject])
        self.assertEqual(code, 0)
        self.assertEqual(projected["subject"], [subject])


if __name__ == "__main__":
    unittest.main()
