"""Public action packaging and the exact upstream functional collector suite."""

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ACTION = Path(__file__).resolve().parents[1] / "actions/npm-package-inventory"


class NpmInventoryActionTests(unittest.TestCase):
    def test_snapshot_provenance_and_actual_module_location(self):
        record = json.loads((ACTION / "UPSTREAM.json").read_text())
        self.assertEqual(record["upstream_commit"], "7ed34abef39277c311eaccc1d08d5bcac1fcc384")
        for name, expected in record["snapshot_files_sha256"].items():
            self.assertEqual(hashlib.sha256((ACTION / name).read_bytes()).hexdigest(), expected)
        self.assertEqual(Path(sys.modules["package_inventory"].__file__).resolve(),
                         (ACTION / "package_inventory.py").resolve())

    def invoke(self, directory, scope, repository="other/repo"):
        root = Path(directory)
        project, temporary = root / "project with spaces", root / "runner temp"
        project.mkdir(); temporary.mkdir()
        output = root / "outputs"
        env = {k: os.environ[k] for k in ("PATH", "SYSTEMROOT") if k in os.environ}
        env.update({"RUNNER_TEMP": str(temporary), "GITHUB_OUTPUT": str(output),
                    "GITHUB_WORKSPACE": str(project), "PROJECT39_INVENTORY_ACTION_PATH": str(ACTION),
                    "PROJECT39_INVENTORY_SCOPE": scope, "GITHUB_ACTIONS": "true",
                    "GITHUB_REPOSITORY": repository, "HOME": str(root)})
        result = subprocess.run(["bash", str(ACTION / "collect.sh")], cwd=root,
                                env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)
        lines = output.read_text().splitlines()
        self.assertEqual(len(lines), 1)
        key, _, path = lines[0].partition("=")
        self.assertEqual(key, "report_path")
        report = Path(path)
        self.assertEqual(report.parent, temporary)
        self.assertEqual(result.stdout, b"")
        return result, json.loads(report.read_text())

    def test_caller_rejection_is_preserved_in_private_report(self):
        with tempfile.TemporaryDirectory() as directory:
            result, report = self.invoke(directory, "core")
            self.assertEqual(result.returncode, 1)
            self.assertEqual(report["status"], "DATA_GAP")
            self.assertEqual(report["reason"], "untrusted_caller_context")

    def test_inputs_cannot_become_shell_commands_or_provider_error_text(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result, report = self.invoke(directory, "core; touch marker")
            self.assertEqual(result.returncode, 1)
            self.assertFalse((root / "marker").exists())
            self.assertEqual(report, {"status": "DATA_GAP", "reason": "invalid_scope"})
            self.assertNotIn(b"touch", result.stderr)

    def test_fixed_root_lockfile_and_missing_input_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            result, report = self.invoke(directory, "proteinaco",
                                         "merglbot-proteinaco/proteinaco-web")
            self.assertEqual(result.returncode, 1)
            self.assertEqual(report["reason"], "lockfile_unreadable")


def load_tests(loader, tests, pattern):
    # Keep original patch targets bound to this actual vendored module, not an
    # installed package or another worktree's similarly named module.
    for name in ("package_inventory", "project39_npm_inventory_upstream_tests"):
        path = ACTION / ("package_inventory.py" if name == "package_inventory"
                         else "test_package_inventory.py")
        if name in sys.modules:
            if Path(sys.modules[name].__file__).resolve() != path.resolve():
                raise RuntimeError("inventory_test_module_binding_conflict")
            module = sys.modules[name]
        else:
            spec = importlib.util.spec_from_file_location(name, path)
            module = importlib.util.module_from_spec(spec)
            sys.modules[name] = module
            spec.loader.exec_module(module)
    tests.addTests(loader.loadTestsFromModule(module))
    return tests
