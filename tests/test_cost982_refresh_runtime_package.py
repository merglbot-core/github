"""Check the owner repair manifest against a clean, isolated runtime directory."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SOURCE = Path(__file__).resolve().parents[1] / "tools" / "cost-budget-report"
MANIFEST = SOURCE / "integration" / "refresh-v2" / "runtime-repair.json"


class RefreshRuntimePackageTest(unittest.TestCase):
    def test_imports_with_only_the_declared_runtime(self):
        manifest = json.loads(MANIFEST.read_bytes())
        self.assertEqual(set(manifest["create_only"]), {"snapshot_adapter.py"})
        self.assertTrue(manifest["create_only"].keys().isdisjoint(manifest["verify_existing"]))
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for name, expected in (manifest["verify_existing"] | manifest["create_only"]).items():
                self.assertEqual(Path(name).name, name)
                content = (SOURCE / name).read_bytes()
                self.assertEqual(hashlib.sha256(content).hexdigest(), expected, name)
                (root / name).write_bytes(content)

            imports = (
                "import sys; sys.path.insert(0, sys.argv[1]); "
                "from refresh_cli import main; from refresh_transaction import refresh_once; "
                "from bounded_transport import gh_get"
            )
            command = [sys.executable, "-I", "-S", "-B", "-c", imports, temp]
            result = subprocess.run(command, cwd=temp, capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)
            (root / "snapshot_adapter.py").unlink()
            result = subprocess.run(command, cwd=temp, capture_output=True, text=True, timeout=15)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("No module named 'snapshot_adapter'", result.stderr)
