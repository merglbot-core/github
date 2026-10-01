"""Static contract of reusable-deploy-runtime-changes.yml and its self-test (merglbot-core/github#909).

The behaviour of the guard script is covered by test_deploy_runtime_changes.py; this file pins how
the reusable workflow wires it: hub script from the pinned hub commit, blobless full-history
checkout of deploy-sha, least-privilege permissions and no `${{ }}` inside a `run:` script.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

WORKFLOWS = Path(__file__).resolve().parents[1] / ".github" / "workflows"
GUARD = WORKFLOWS / "reusable-deploy-runtime-changes.yml"
SELFTEST = WORKFLOWS / "deploy-guard-selftest.yml"


def run_scripts(text: str) -> list[str]:
    """Bodies of every `run:` step (inline or block scalar)."""
    return re.findall(r"^( *)run: (?:\|\n((?:\1  .*\n|\n)+)|(.*)$)", text, re.M)


class GuardWorkflowContractTests(unittest.TestCase):
    def test_interface(self) -> None:
        text = GUARD.read_text(encoding="utf-8")
        for name in ("deploy-sha", "workflow-file", "deploy-job-regex", "deny-extra",
                     "force-runtime-extra", "runs-on", "dry-run", "base-override"):
            self.assertRegex(text, rf"\n      {re.escape(name)}:\n")
        for name in ("runtime", "reason", "base-sha"):
            self.assertIn(f"value: ${{{{ jobs.runtime-changes.outputs.{name} }}}}", text)
        self.assertIn("default: '^deploy$'", text)
        self.assertIn("default: 'ubuntu-24.04'", text)

    def test_checkouts_permissions_and_env_only_values(self) -> None:
        text = GUARD.read_text(encoding="utf-8")
        for needle in ("ref: ${{ inputs.deploy-sha }}", "fetch-depth: 0", "filter: blob:none",
                       "JOB_WORKFLOW_SHA: ${{ job.workflow_sha }}", "sparse-checkout: scripts/deploy-guard",
                       "scripts/deploy-guard/runtime_changes.sh", "actions: read", "contents: read",
                       "RC_WORKFLOW_REF: ${{ github.workflow_ref }}", "GH_TOKEN: ${{ github.token }}"):
            self.assertIn(needle, text)
        self.assertEqual(2, text.count("persist-credentials: false"))
        self.assertNotRegex(text, r"(?m)^\s+(contents|actions|id-token): write")
        scripts = run_scripts(text) + run_scripts(SELFTEST.read_text(encoding="utf-8"))
        self.assertEqual(4, len(scripts))  # hub ref, decide, merge base, assert
        for match in scripts:
            self.assertNotIn("${{", "".join(match[1:]))

    def test_selftest_runs_dry_on_both_runner_labels(self) -> None:
        text = SELFTEST.read_text(encoding="utf-8")
        self.assertIn("pull_request:", text)
        self.assertNotRegex(text, r"(?m)^  (push|schedule|workflow_dispatch):")
        self.assertIn("'scripts/deploy-guard/**'", text)
        self.assertEqual(3, text.count("uses: ./.github/workflows/reusable-deploy-runtime-changes.yml"))
        self.assertEqual(3, text.count("dry-run: true"))
        self.assertIn("runs-on: ubuntu-24.04", text)
        self.assertIn("runs-on: ubuntu-slim", text)
        self.assertIn("'tests/test_deploy_runtime_changes_workflow.py'", text)

    def test_selftest_requires_a_path_classification_against_the_merge_base(self) -> None:
        # V6 on #973: a PR behind main made the base diverge and the self-test accepted
        # fail-open:diverged, so no path was ever classified.
        text = SELFTEST.read_text(encoding="utf-8")
        self.assertIn('git merge-base "$BASE_SHA" "$HEAD_SHA"', text)
        self.assertEqual(2, text.count("base-override: ${{ needs.merge-base.outputs.sha }}"))
        self.assertNotIn("base-override: ${{ github.event.pull_request.base.sha }}", text)
        self.assertIn("$reason == runtime:?*", text)
        self.assertIn("$reason == non-runtime-only", text)
        self.assertNotIn("fail-open:error*", text)
        self.assertIn("rerun() { [[ $RUN_ATTEMPT != 1 && $1 == true && $2 == rerun ]]; }", text)


if __name__ == "__main__":
    unittest.main()
