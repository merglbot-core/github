import importlib.util
import pathlib
import tempfile
import unittest

HERE = pathlib.Path(__file__).parent
spec = importlib.util.spec_from_file_location("cost_install", HERE / "install.py")
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class Installation(unittest.TestCase):
    def test_install_and_rollback_preserve_state(self):
        with tempfile.TemporaryDirectory() as temp:
            base = pathlib.Path(temp)
            installer.BASE = base
            installer.AUTO = base / "autopilot"
            installer.CODE = base / "autopilot.py"
            installer.MODULE = base / "cost_semantic.py"
            installer.STATE = base / "state.json"
            installer.HOLD = base / "OWNER_HOLD"
            live_source = pathlib.Path.home() / ".merglbot/gh-cost-910/autopilot.py"
            if live_source.exists():
                old_source = live_source.read_bytes()
            else:
                patch_spec = importlib.util.spec_from_file_location("cost_patch", HERE / "patch_autopilot.py")
                patch_module = importlib.util.module_from_spec(patch_spec)
                patch_spec.loader.exec_module(patch_module)
                old_source = ("def measure_no_push_runs(state, item):\n"
                              "    item[\"push_runs\"] = 0\n"
                              "    note = 'per_page=50'\n"
                              "\n\ndef measure_file_state(state, item):\n"
                              + patch_module.OLD_MISSING).encode()
            old_state = b'{"registration_complete": false, "dod": {}}\n'
            installer.CODE.write_bytes(old_source)
            installer.STATE.write_bytes(old_state)
            expected = installer.digest(old_source)
            dry = installer.install(expected, True)
            self.assertTrue(dry["dry_run"])
            self.assertEqual(installer.CODE.read_bytes(), old_source)
            receipt = installer.install(expected, False)
            self.assertTrue(receipt["state_unchanged"])
            self.assertNotEqual(installer.CODE.read_bytes(), old_source)
            self.assertEqual(installer.STATE.read_bytes(), old_state)
            with self.assertRaises(RuntimeError):
                installer.install(expected, False)
            original_module = installer.MODULE.read_bytes()
            installer.MODULE.write_bytes(b"newer module")
            with self.assertRaises(RuntimeError):
                installer.rollback(pathlib.Path(receipt["backup"]))
            self.assertNotEqual(installer.CODE.read_bytes(), old_source)
            installer.MODULE.write_bytes(original_module)
            restored = installer.rollback(pathlib.Path(receipt["backup"]))
            self.assertEqual(restored["restored_source_sha256"], expected)
            self.assertEqual(installer.CODE.read_bytes(), old_source)
            self.assertEqual(installer.STATE.read_bytes(), old_state)

    def test_owner_hold_and_occupied_lock_fail_before_write(self):
        with tempfile.TemporaryDirectory() as temp:
            base = pathlib.Path(temp)
            installer.BASE = base
            installer.AUTO = base / "autopilot"
            installer.CODE = base / "autopilot.py"
            installer.MODULE = base / "cost_semantic.py"
            installer.STATE = base / "state.json"
            installer.HOLD = base / "OWNER_HOLD"
            installer.CODE.write_bytes(b"unchanged")
            installer.STATE.write_bytes(b"{}")
            installer.HOLD.touch()
            with self.assertRaises(RuntimeError):
                installer.install(installer.digest(b"unchanged"), False)
            installer.HOLD.unlink()
            (installer.AUTO / "lock").mkdir(parents=True)
            with self.assertRaises(FileExistsError):
                installer.install(installer.digest(b"unchanged"), False)
            self.assertEqual(installer.CODE.read_bytes(), b"unchanged")


if __name__ == "__main__":
    unittest.main()
