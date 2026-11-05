"""Offline CLI contract: no real gh binary or network access is used."""
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
V6 = "Merglbot PR Assistant v6"
APP = 3518182


class RequiredCheckPublishersTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.scripts = self.root / "scripts" / "governance"
        shutil.copytree(ROOT / "scripts" / "governance", self.scripts)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        gh = self.bin / "gh"
        gh.write_text("""#!/usr/bin/env python3
import json, os, pathlib, sys
p = pathlib.Path(os.environ['MOCK_ROOT'])
a = sys.argv[1:]
with (p / 'calls.jsonl').open('a') as f:
    f.write(json.dumps(a) + '\\n')
if not a or a[0] != 'api':
    raise SystemExit('unexpected gh command')
if (p / 'deny-read').exists():
    raise SystemExit('simulated read refusal')
if '-X' in a and a[a.index('-X') + 1] == 'PUT':
    payload = json.loads(pathlib.Path(a[a.index('--input') + 1]).read_text())
    (p / 'payload.json').write_text(json.dumps(payload))
    raise SystemExit(0)
if any(x.endswith('/protection') for x in a):
    path = p / 'before.json'
    if (p / 'payload.json').exists():
        path = p / ('after.json' if (p / 'after.json').exists() else 'payload.json')
    print(path.read_text())
elif '--jq' in a and a[a.index('--jq') + 1] == '.protected':
    print('true')
else:
    raise SystemExit('unexpected API read')
""")
        gh.chmod(0o755)
        self.env = dict(os.environ, MOCK_ROOT=str(self.root),
                        PATH=f"{self.bin}:{os.environ['PATH']}")

    def before(self, contexts, checks):
        self.protection = {"required_status_checks": {
            "strict": True, "contexts": contexts, "checks": checks},
            "enforce_admins": {"enabled": True}}
        (self.root / "before.json").write_text(json.dumps(self.protection))

    def run_cli(self, *args, wrapper=False):
        script = "apply-v5-required-check.sh" if wrapper else "update-branch-protection.sh"
        return subprocess.run(["bash", str(self.scripts / script), "--repo", "test/repo",
                               "--branch", "main", *args], env=self.env,
                              cwd=self.root, capture_output=True, text=True, timeout=20,
                              check=False)

    def expected_checks(self):
        paths = list(self.root.glob("tmp/agent/branch-protection/*/*/*.payload.json"))
        self.assertEqual(len(paths), 1)
        return json.loads(paths[0].read_text())["required_status_checks"]

    def test_retains_explicit_publishers_and_pins_added_v6(self):
        self.before(["ci"], [{"context": "ci", "app_id": 101}])
        result = self.run_cli("--check", "ci", "--check", V6, "--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.expected_checks(), {"strict": True, "checks": [
            {"context": "ci", "app_id": 101}, {"context": V6, "app_id": APP}]})
        self.assertFalse((self.root / "payload.json").exists())

    def test_no_check_arguments_preserve_multiple_bindings_and_wildcard(self):
        checks = [{"context": "ci", "app_id": 101}, {"context": "ci", "app_id": 202},
                  {"context": "legacy", "app_id": -1}]
        self.before(["ci", "legacy"], checks)
        result = self.run_cli("--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.expected_checks()["checks"], checks)

    def test_unresolved_publisher_fails_before_put(self):
        for checks in ([], [{"context": "ci", "app_id": None}]):
            with self.subTest(checks=checks):
                (self.root / "payload.json").unlink(missing_ok=True)
                self.before(["ci"], checks)
                result = self.run_cli("--apply", "--yes")
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse((self.root / "payload.json").exists())

    def test_unknown_new_context_fails_before_put(self):
        self.before(["ci"], [{"context": "ci", "app_id": 101}])
        result = self.run_cli("--check", "unknown", "--apply", "--yes")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.root / "payload.json").exists())

    def test_wrapper_repairs_wrong_v6_publisher_and_keeps_checks_only_context(self):
        self.before([V6], [{"context": V6, "app_id": 999},
                           {"context": "ci", "app_id": 101}])
        result = self.run_cli("--dry-run", wrapper=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertCountEqual(self.expected_checks()["checks"], [
            {"context": V6, "app_id": APP}, {"context": "ci", "app_id": 101}])

    def test_wrapper_skips_correct_v6_without_write(self):
        self.before([V6], [{"context": V6, "app_id": APP}])
        result = self.run_cli("--apply", "--yes", wrapper=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.root / "payload.json").exists())
        self.assertIn("already=1", result.stdout)

    def test_readback_publisher_change_is_failure_and_visible_in_diff(self):
        self.before(["ci"], [{"context": "ci", "app_id": 101}])
        after = json.loads(json.dumps(self.protection))
        after["required_status_checks"]["checks"][0]["app_id"] = 202
        (self.root / "after.json").write_text(json.dumps(after))
        result = self.run_cli("--apply", "--yes")
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue((self.root / "payload.json").exists())
        diffs = list(self.root.glob("tmp/agent/branch-protection/*/*/*.diff.txt"))
        self.assertIn('"app_id": 202', diffs[0].read_text())

    def test_check_replacement_removes_only_unrequested_context(self):
        self.before(["ci", "old"], [{"context": "ci", "app_id": 101},
                                    {"context": "old", "app_id": 202}])
        result = self.run_cli("--check", "ci", "--apply", "--yes")
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads((self.root / "payload.json").read_text())
        self.assertEqual(payload["required_status_checks"]["checks"], [
            {"context": "ci", "app_id": 101}])
        self.assertTrue(payload["enforce_admins"])

    def test_read_failure_never_puts(self):
        self.before(["ci"], [{"context": "ci", "app_id": 101}])
        (self.root / "deny-read").touch()
        result = self.run_cli("--apply", "--yes")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.root / "payload.json").exists())

    def test_readback_does_not_normalize_foreign_v6_to_success(self):
        self.before([V6], [{"context": V6, "app_id": 999}])
        (self.root / "after.json").write_text(json.dumps(self.protection))
        result = self.run_cli("--apply", "--yes")
        self.assertNotEqual(result.returncode, 0)
        payload = json.loads((self.root / "payload.json").read_text())
        self.assertEqual(payload["required_status_checks"]["checks"], [
            {"context": V6, "app_id": APP}])

    def test_null_protection_can_add_only_known_v6_binding(self):
        (self.root / "before.json").write_text('{"required_status_checks": null}')
        result = self.run_cli("--check", V6, "--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.expected_checks()["checks"], [
            {"context": V6, "app_id": APP}])

    def test_invalid_publisher_and_empty_checks_fail_before_put(self):
        for app in (True, 0, -2, "101"):
            with self.subTest(app=app):
                self.before(["ci"], [{"context": "ci", "app_id": app}])
                result = self.run_cli("--apply", "--yes")
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse((self.root / "payload.json").exists())
        self.before([], [])
        result = self.run_cli("--apply", "--yes")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.root / "payload.json").exists())

    def test_correct_and_foreign_v6_bindings_do_not_skip_repair(self):
        self.before([V6], [{"context": V6, "app_id": APP},
                           {"context": V6, "app_id": 999}])
        result = self.run_cli("--dry-run", wrapper=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.expected_checks()["checks"], [
            {"context": V6, "app_id": APP}])

    def test_drift_output_is_rejected_by_actual_workflow_guard(self):
        workflow = yaml.safe_load((ROOT / ".github/workflows/v5-required-check-sync.yml").read_text())
        steps = [step for job in workflow["jobs"].values() for step in job["steps"]]
        guard = next(step["run"] for step in steps
                     if step.get("name") == "Fail on drift (dry-run only)")
        log = self.root / "v6sync.log"
        # Run the unchanged caller guard with only its log path sandboxed.
        guard = guard.replace("/tmp/v6sync.log", str(log))
        for checks in ([{"context": "ci", "app_id": 101}],
                       [{"context": V6, "app_id": 999}]):
            with self.subTest(checks=checks):
                self.before([check["context"] for check in checks], checks)
                result = self.run_cli("--dry-run", wrapper=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                log.write_text(result.stdout)
                guard_result = subprocess.run(["bash", "-c", guard], cwd=self.root,
                                              env=self.env, capture_output=True,
                                              text=True, timeout=10, check=False)
                self.assertEqual(guard_result.returncode, 1, guard_result.stdout)
                self.assertIn("missing v6: 1", guard_result.stdout)
                self.assertFalse((self.root / "payload.json").exists())


if __name__ == "__main__":
    unittest.main()
