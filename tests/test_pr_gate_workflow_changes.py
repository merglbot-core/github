"""Exercise the path selector shipped in the reusable PR Gate."""
import os
from pathlib import Path
import subprocess
import tempfile
import textwrap
import unittest


WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/pr-gate.yml"


class WorkflowChangesTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.repo = Path(temp.name)
        workflow = WORKFLOW.read_text()
        step = workflow.split("      - name: Detect workflow changes\n", 1)[1]
        self.script = textwrap.dedent(step.split("        run: |\n", 1)[1]
                                      .split("\n      - name:", 1)[0])
        self.git("init", "--quiet")
        self.git("commit", "--allow-empty", "-qm", "base")
        self.base = self.git("rev-parse", "HEAD")

    def git(self, *args):
        return subprocess.check_output(
            ["git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
             "-c", "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null", *args],
            cwd=self.repo, text=True,
        ).strip()

    def select(self, base):
        output = self.repo / ".step-output"
        output.write_text("")
        process = subprocess.run(
            ["bash", "-c", self.script], cwd=self.repo, capture_output=True,
            env={**os.environ, "BASE_SHA": base, "GITHUB_OUTPUT": str(output)},
        )
        return process.returncode, output.read_text().strip()

    def test_helper_requires_successful_trusted_checkout(self):
        workflow = WORKFLOW.read_text()
        checkout = workflow.split("id: trusted_hub_checkout", 1)
        self.assertIn("uses: actions/checkout@", checkout[0].splitlines()[-2])
        self.assertIn("path: .docs-governance-hub", checkout[1].split("      - name:", 1)[0])
        lint = workflow.split("id: workflow_lint\n", 1)[1].split("      - name:", 1)[0]
        self.assertIn("steps.trusted_hub_checkout.outcome == 'success'", lint)

    def test_lint_refuses_missing_foreign_or_mismatched_called_workflow(self):
        lint = WORKFLOW.read_text().split("id: workflow_lint\n", 1)[1].split("      - name:", 1)[0]
        guard = textwrap.dedent(lint.split("        run: |\n", 1)[1].split("          python3", 1)[0])
        (self.repo / ".docs-governance-hub").mkdir()
        self.git("init", "-q", ".docs-governance-hub")
        self.git("-C", ".docs-governance-hub", "commit", "--allow-empty", "-qm", "hub")
        head = self.git("-C", ".docs-governance-hub", "rev-parse", "HEAD")
        for repo, sha, expected in [("", "", 1), ("other/repo", head, 1),
                                    ("merglbot-core/github", "0" * 40, 1),
                                    ("merglbot-core/github", head, 0)]:
            with self.subTest(repo=repo, sha=sha):
                result = subprocess.run(["bash", "-c", guard], cwd=self.repo, capture_output=True,
                                        env={**os.environ, "CALLED_WORKFLOW_REPO": repo,
                                             "CALLED_WORKFLOW_SHA": sha, "GITHUB_SHA": head})
                self.assertEqual(result.returncode, expected)

    def test_change_scope(self):
        (self.repo / "notes.md").write_text("notes\n")
        self.git("add", "notes.md")
        self.git("commit", "-qm", "docs")
        self.assertEqual(self.select(self.base), (0, "changed=false"))
        path = ".github/workflows/ci.yml"
        (self.repo / path).parent.mkdir(parents=True)
        (self.repo / path).write_text("name: CI\n")
        self.git("add", path)
        self.git("commit", "-qm", "new workflow")
        self.assertEqual(self.select(self.base), (0, "changed=true"))
        before_delete = self.git("rev-parse", "HEAD")
        self.git("rm", "-q", path)
        self.git("commit", "-qm", "remove workflow")
        self.assertEqual(self.select(before_delete), (0, "changed=true"))

    def test_unusable_base_reports_failure_without_skip(self):
        for base in ("", "0" * 40):
            with self.subTest(base=base):
                status, output = self.select(base)
                self.assertNotEqual(status, 0)
                self.assertEqual(output, "")
