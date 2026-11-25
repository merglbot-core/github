"""Exercise the reusable gate's handling of the optional lint tool installer."""

import os
from pathlib import Path
import re
import subprocess
import textwrap
import unittest


WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/pr-gate.yml"


class WorkflowLintInstallerTests(unittest.TestCase):
    def test_scan_requires_successful_opt_in_install(self):
        source = WORKFLOW.read_text()
        install = source.split("      - name: Install actionlint and zizmor\n")[1].split("      - name:")[0]
        self.assertIn("id: workflow_lint_install\n", install)
        self.assertIn("if: ${{ !cancelled() && inputs['workflow-lint'] }}", install)
        scan = source.split("      - name: Workflow lint (actionlint + zizmor)\n")[1].split("      - name:")[0]
        self.assertIn("if: ${{ !cancelled() && steps.workflow_lint_install.outcome == 'success' }}", scan)

    def test_install_and_scan_outcomes_reach_final_gate(self):
        step = WORKFLOW.read_text().split("      - name: Check gate results\n")[1]
        header, script = step.split("        run: |\n", 1)
        self.assertIn("WORKFLOW_LINT_INSTALL: ${{ steps.workflow_lint_install.outcome }}", header)
        self.assertIn("WORKFLOW_LINT: ${{ steps.workflow_lint.outcome }}", header)
        outcomes = dict.fromkeys(re.findall(r"^          ([A-Z_]+):", header, re.M), "success")
        for install, scan, expected in [
            ("failure", "skipped", 1), ("skipped", "skipped", 0),
            ("success", "failure", 1), ("success", "success", 0),
        ]:
            with self.subTest(install=install, scan=scan):
                result = subprocess.run(
                    ["bash", "-c", textwrap.dedent(script)],
                    env={**os.environ, **outcomes, "WORKFLOW_LINT_INSTALL": install, "WORKFLOW_LINT": scan},
                    capture_output=True, text=True,
                )
                self.assertEqual(result.returncode, expected, result.stderr)
