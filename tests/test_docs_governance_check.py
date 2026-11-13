"""Include the docs-governance classifier fixture suite in the Python CI discovery."""
from pathlib import Path
import shutil
import subprocess
import unittest


class DocsGovernanceCheckTests(unittest.TestCase):
    def test_docs_governance_fixture_suite(self):
        bash = shutil.which("bash")
        self.assertIsNotNone(bash, "bash is required for the docs-governance fixture suite")
        self.assertIsNotNone(shutil.which("git"), "git is required for the docs-governance fixture suite")
        self.assertIsNotNone(shutil.which("node"), "Node runtime is required for docs-governance check.mjs")
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run(
            [bash, "tests/docs-governance/run.sh"],
            cwd=root,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=300,
            check=False,
        )
        # Only the per-case PASS/FAIL lines and the summary are surfaced on failure.
        summary = "\n".join(
            line for line in result.stdout.splitlines()
            if line.startswith(("PASS ", "FAIL ", "pass="))
        )
        self.assertEqual(result.returncode, 0, f"docs-governance fixture suite failed:\n{summary}")
        self.assertIn("fail=0", result.stdout)


if __name__ == "__main__":
    unittest.main()
