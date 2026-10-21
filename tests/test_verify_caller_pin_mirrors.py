"""Runs scripts/verify-caller-pin-mirrors.py over scratch workflow trees (github#773)."""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "verify-caller-pin-mirrors.py"
NEW = "1b83d5857c3486e62d5207aa87551f2792647c38"
OLD = "a71d7d0dca646b1d6564d1454ace4f9ebc27417c"


def run(workflow: str) -> subprocess.CompletedProcess:
    with tempfile.TemporaryDirectory() as tmp:
        wf = Path(tmp) / ".github" / "workflows"
        wf.mkdir(parents=True)
        (wf / "ci.yml").write_text(workflow, encoding="utf-8")
        return subprocess.run([sys.executable, str(SCRIPT), tmp], capture_output=True, text=True)


class CallerPinMirrors(unittest.TestCase):
    def test_matching_mirror_passes(self) -> None:
        proc = run(f"# Thin caller pinned to merglbot-core/github@{NEW}.\njobs:\n  ci:\n    uses: merglbot-core/github/.github/workflows/ci-python.yml@{NEW}\n")
        self.assertEqual(proc.returncode, 0, proc.stdout)

    def test_short_sha_mirror_of_the_running_pin_passes(self) -> None:
        proc = run(f"# pinned to merglbot-core/github@{NEW[:8]}\njobs:\n  ci:\n    uses: merglbot-core/github/.github/workflows/ci-python.yml@{NEW}\n")
        self.assertEqual(proc.returncode, 0, proc.stdout)

    def test_stale_mirror_after_a_bump_fails(self) -> None:
        proc = run(f"# Thin caller pinned to merglbot-core/github@{OLD}.\njobs:\n  ci:\n    uses: merglbot-core/github/.github/workflows/ci-python.yml@{NEW}\n")
        self.assertEqual(proc.returncode, 1)
        self.assertIn(f"merglbot-core/github@{OLD}", proc.stdout)

    def test_stale_mirror_inside_a_block_scalar_fails(self) -> None:
        proc = run(f"on:\n  workflow_dispatch:\n    inputs:\n      x:\n        description: |\n          Calls merglbot-core/github@{OLD}.\njobs:\n  ci:\n    uses: merglbot-core/github/.github/workflows/ci-python.yml@{NEW}\n")
        self.assertEqual(proc.returncode, 1, proc.stdout)

    def test_stale_comment_on_the_uses_line_fails(self) -> None:
        proc = run(f"jobs:\n  ci:\n    uses: merglbot-core/github/.github/workflows/ci-python.yml@{NEW}  # was merglbot-core/github@{OLD}\n")
        self.assertEqual(proc.returncode, 1, proc.stdout)

    def test_uses_text_in_a_block_scalar_is_not_a_running_pin(self) -> None:
        proc = run(f"on:\n  workflow_dispatch:\n    inputs:\n      x:\n        description: |\n          uses: merglbot-core/github/.github/workflows/ci-python.yml@{OLD}\n          pinned to merglbot-core/github@{OLD}\njobs:\n  ci:\n    uses: merglbot-core/github/.github/workflows/ci-python.yml@{NEW}\n")
        self.assertEqual(proc.returncode, 1, proc.stdout)

    def test_lone_uses_text_in_a_block_scalar_fails(self) -> None:
        proc = run(f"on:\n  workflow_dispatch:\n    inputs:\n      x:\n        description: |\n          uses: merglbot-core/github/.github/workflows/ci-python.yml@{OLD}\njobs:\n  ci:\n    uses: merglbot-core/github/.github/workflows/ci-python.yml@{NEW}\n")
        self.assertEqual(proc.returncode, 1, proc.stdout)

    def test_quoted_uses_value_is_a_running_pin(self) -> None:
        proc = run(f"# pinned to merglbot-core/github@{NEW}\njobs:\n  ci:\n    uses: 'merglbot-core/github/.github/workflows/ci-python.yml@{NEW}'\n")
        self.assertEqual(proc.returncode, 0, proc.stdout)

    def test_unparsable_workflow_fails_closed(self) -> None:
        proc = run(f"jobs:\n  ci:\n    uses: merglbot-core/github/.github/workflows/ci-python.yml@{NEW}\n   bad: [\n")
        self.assertEqual(proc.returncode, 1, proc.stdout)
        self.assertIn("does not parse", proc.stdout)

    def test_file_without_mirror_comment_passes(self) -> None:
        proc = run(f"jobs:\n  ci:\n    uses: merglbot-core/github/.github/workflows/ci-python.yml@{NEW}  # v1\n")
        self.assertEqual(proc.returncode, 0, proc.stdout)


if __name__ == "__main__":
    unittest.main()
