"""Include release helper behavior in the existing Python CI discovery."""
from pathlib import Path
import shutil
import subprocess
import unittest


class ReleaseVersionTests(unittest.TestCase):
    def test_node_release_behavior_suite(self):
        node = shutil.which("node")
        self.assertIsNotNone(node, "Node runtime is required for release behavior tests")
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run(
            [node, "--test", "tests/release-version.test.mjs"],
            cwd=root,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=30,
            check=False,
        )
        self.assertEqual(result.returncode, 0, "Release version behavior suite failed")


if __name__ == "__main__":
    unittest.main()
