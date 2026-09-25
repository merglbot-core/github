import importlib.util
import json
import pathlib
import tempfile
import unittest

HERE = pathlib.Path(__file__).parent
spec = importlib.util.spec_from_file_location("closeout_install", HERE / "install.py")
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class Installation(unittest.TestCase):
    def test_migration_preserves_unrelated_state_and_rollback_checks_drift(self):
        with tempfile.TemporaryDirectory() as temp:
            base = pathlib.Path(temp)
            installer.BASE = base
            installer.LOCK = base / "autopilot/lock"
            installer.LOCK.parent.mkdir()
            installer.CODE = base / "autopilot.py"
            installer.STATE = base / "state.json"
            installer.HOLD = base / "OWNER_HOLD"
            original_code = (HERE / "fixture_legacy.py").read_bytes()
            original_state = (json.dumps({"subs": {"921": {"board_done_at": "old"}, "913": {}},
                                          "dod": {"913|repo": {"met_at": "ok"}},
                                          "registration_complete": True}, indent=2) + "\n").encode()
            installer.CODE.write_bytes(original_code)
            installer.STATE.write_bytes(original_state)
            code_sha, state_sha = installer.digest(original_code), installer.digest(original_state)
            self.assertTrue(installer.install(code_sha, state_sha, True)["dry_run"])
            self.assertEqual(installer.STATE.read_bytes(), original_state)
            receipt = installer.install(code_sha, state_sha, False)
            state = json.loads(installer.STATE.read_bytes())
            self.assertTrue(state["subs"]["921"]["technical_hold"])
            self.assertIsNone(state["subs"]["921"]["board_done_at"])
            self.assertEqual(state["dod"]["913|repo"]["met_at"], "ok")
            with self.assertRaises(RuntimeError):
                installer.install(code_sha, state_sha, False)
            backup = pathlib.Path(receipt["backup"])
            new_state = installer.STATE.read_bytes()
            installer.STATE.write_bytes(new_state + b" ")
            with self.assertRaises(RuntimeError):
                installer.rollback(backup)
            installer.STATE.write_bytes(new_state)
            original_backup = (backup / "state.json").read_bytes()
            (backup / "state.json").write_bytes(b"corrupt")
            with self.assertRaises(RuntimeError):
                installer.rollback(backup)
            (backup / "state.json").write_bytes(original_backup)
            result = installer.rollback(backup)
            self.assertEqual(result["restored_code_sha256"], code_sha)
            self.assertEqual(installer.STATE.read_bytes(), original_state)

    def test_hold_and_live_lock_block_install(self):
        with tempfile.TemporaryDirectory() as temp:
            base = pathlib.Path(temp)
            installer.BASE = base
            installer.LOCK = base / "autopilot/lock"
            installer.LOCK.parent.mkdir()
            installer.CODE = base / "autopilot.py"
            installer.STATE = base / "state.json"
            installer.HOLD = base / "OWNER_HOLD"
            installer.CODE.write_bytes(b"unchanged")
            installer.STATE.write_bytes(b"{}")
            installer.HOLD.touch()
            with self.assertRaises(RuntimeError):
                installer.install(installer.digest(b"unchanged"), installer.digest(b"{}"), False)
            installer.HOLD.unlink()
            installer.LOCK.mkdir()
            with self.assertRaises(FileExistsError):
                installer.install(installer.digest(b"unchanged"), installer.digest(b"{}"), False)
            self.assertEqual(installer.CODE.read_bytes(), b"unchanged")


if __name__ == "__main__":
    unittest.main()
