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
