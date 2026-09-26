import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).parent
spec = importlib.util.spec_from_file_location("installer", HERE / "install.py")
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class InstallTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        (self.base / "autopilot").mkdir()
        self.code = self.base / "autopilot.py"
        self.state = self.base / "state.json"
        self.code.write_text('def billing_acceptance(state):\n'
                             '    billing = state.setdefault("billing", {})\n'
                             '    if not billing.get("due_at"):\n        return False\n'
                             '    return post_billing(state, billing)\n\n'
                             'def post_billing(state, billing):\n'
                             '    repos = sorted({key.rsplit("#", 1)[0] for key, pr in state["prs"].items() if pr.get("merged_at")}\n'
                             '                   | set(billing.get("extra_repos", [])))\n'
                             '    return repos\n')
        self.original = {"prs": {}, "subs": {"921": {"technical_hold": False,
                         "board_done_at": "owner"}}, "dod": {"proof": {"met_at": "natural"}},
                         "registration_complete": True, "billing": {"due_at": "old"}}
        self.state.write_text(json.dumps(self.original))
        self.old_code, self.old_state = self.code.read_bytes(), self.state.read_bytes()
        self.followups = {"org/repo#1": {"merged_at": "2026-09-26T17:43:21Z",
            "merge_sha": "a" * 40, "base_ref": "main", "scope_issue": "merglbot-core/github#921"}}
        for name, value in (("BASE", self.base), ("HOLD", self.base / "OWNER_HOLD")):
            context = patch.object(installer, name, value)
            context.start()
            self.addCleanup(context.stop)
        context = patch.object(installer, "required_followups", return_value={"org/repo#1"})
        context.start()
        self.addCleanup(context.stop)

    def install(self, dry=False):
        return installer.install(installer.digest(self.old_code), installer.digest(self.old_state), self.followups, dry)

    def test_install_preserves_owner_decision_and_dod(self):
        result = self.install()
        current = json.loads(self.state.read_bytes())
        for key, value in self.original.items():
            self.assertEqual(current[key], value)
        self.assertEqual(current["billing_followups"], self.followups)
        backup = Path(result["backup"])
        self.assertEqual((backup / "autopilot.py").read_bytes(), self.old_code)
        self.assertEqual((backup / "state.json").read_bytes(), self.old_state)
        self.assertFalse((self.base / "autopilot/lock").exists())

    def test_later_registration_accepts_exact_patch_and_preserves_newer_dod(self):
        self.install()
        current = json.loads(self.state.read_bytes())
        current["dod"]["proof"]["met_at"] = "new natural tick"
        self.state.write_text(json.dumps(current))
        self.old_code, self.old_state = self.code.read_bytes(), self.state.read_bytes()
        self.followups["org/repo#2"] = {**self.followups["org/repo#1"], "merged_at": "2026-09-27T17:00:00Z"}
        self.install()
        self.assertEqual(self.code.read_bytes(), self.old_code)
        result = json.loads(self.state.read_bytes())
        self.assertEqual(result["dod"], current["dod"])
        self.assertEqual(set(result["billing_followups"]), {"org/repo#1", "org/repo#2"})

    def test_dry_run_does_not_write(self):
        self.install(True)
        self.assertEqual(self.code.read_bytes(), self.old_code)
        self.assertEqual(self.state.read_bytes(), self.old_state)
        self.assertFalse((self.base / "backups").exists())

    def test_empty_or_partial_batch_is_rejected_before_network_or_writes(self):
        with patch.object(installer, "required_followups", return_value={"org/repo#1", "org/repo#2"}), \
                patch.object(installer.subprocess, "run") as run:
            for keys in ([], ["org/repo#1"]):
                with self.assertRaises(ValueError):
                    installer.verify_followups(keys)
            run.assert_not_called()
            with self.assertRaises(ValueError):
                self.install()
        self.assertEqual(self.code.read_bytes(), self.old_code)

    def test_missing_metadata_requires_exact_registered_head_proof(self):
        key = "original/repo#4"
        self.original["prs"][key] = {"head": "b" * 40, "state": "verified", "merged_at": None,
                                     "merge_sha": None, "owner_note": "keep"}
        self.state.write_text(json.dumps(self.original))
        self.old_state = self.state.read_bytes()
        with self.assertRaises(RuntimeError):
            self.install()
        proof = {key: {"head": "c" * 40, "merged_at": "2026-09-23T21:05:38Z", "merge_sha": "d" * 40}}
        with self.assertRaises(RuntimeError):
            installer.install(installer.digest(self.old_code), installer.digest(self.old_state), self.followups, repairs=proof)
        proof[key]["head"] = "b" * 40
        installer.install(installer.digest(self.old_code), installer.digest(self.old_state), self.followups, repairs=proof)
        current = json.loads(self.state.read_bytes())
        self.assertEqual(current["prs"][key]["state"], "verified")
        self.assertEqual(current["prs"][key]["owner_note"], "keep")
        self.assertEqual(current["prs"][key]["merged_at"], proof[key]["merged_at"])
        self.assertEqual(current["subs"], self.original["subs"])

    def test_drift_owner_hold_lock_and_posted_acceptance_stop_install(self):
        self.state.write_text(json.dumps({**self.original, "newer_tick": True}))
        with self.assertRaises(RuntimeError):
            self.install()
        self.state.write_bytes(self.old_state)
        hold = self.base / "OWNER_HOLD"
        hold.touch()
        with self.assertRaises(RuntimeError):
            self.install()
        hold.unlink()
        lock = self.base / "autopilot/lock"
        lock.mkdir()
        with self.assertRaises(FileExistsError):
            self.install()
        lock.rmdir()
        self.original["billing"]["posted_at"] = "published"
        self.state.write_text(json.dumps(self.original))
        self.old_state = self.state.read_bytes()
        with self.assertRaises(RuntimeError):
            self.install()
        self.assertEqual(self.code.read_bytes(), self.old_code)

    def test_failure_rolls_back_both_files_without_changing_newer_dod(self):
        original_atomic = installer.atomic
        def fail_once(path, data):
            if path == self.state and data != self.old_state:
                raise OSError("simulated replacement failure")
            original_atomic(path, data)
        with patch.object(installer, "atomic", fail_once):
            with self.assertRaises(OSError):
                self.install()
        self.assertEqual(self.code.read_bytes(), self.old_code)
        self.assertEqual(self.state.read_bytes(), self.old_state)


if __name__ == "__main__":
    unittest.main()
