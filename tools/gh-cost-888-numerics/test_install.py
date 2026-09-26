import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).parent
spec = importlib.util.spec_from_file_location("installer888", HERE / "install.py")
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
        self.original_code = (HERE / "fixture_post_billing.py").read_bytes()
        self.code.write_bytes(self.original_code)
        context = patch.object(installer, "BASE", self.base)
        context.start()
        self.addCleanup(context.stop)
        original_load = installer.load
        def isolated_load(name, path):
            module = original_load(name, path)
            if name == "window_install":
                module.HOLD = self.base / "OWNER_HOLD"
            return module
        context = patch.object(installer, "load", side_effect=isolated_load)
        context.start()
        self.addCleanup(context.stop)

    def activate(self, fixture, dry=False):
        self.original_state = json.dumps(fixture).encode()
        self.state.write_bytes(self.original_state)
        return installer.install(hashlib.sha256(self.original_code).hexdigest(),
                                 hashlib.sha256(self.original_state).hexdigest(), dry)

    def test_real_install_targets888_only_and_does_not_rewind_state(self):
        fixture = {"billing": {}, "dod": {"natural": "preserved"}, "owner_decision": "keep"}
        result = self.activate(fixture, True)
        self.assertTrue(result["dry_run"])
        self.assertEqual(self.code.read_bytes(), self.original_code)
        result = self.activate(fixture)
        self.assertNotEqual(self.code.read_bytes(), self.original_code)
        self.assertEqual(self.state.read_bytes(), self.original_state)
        self.assertTrue(Path(result["backup"]).is_relative_to(self.base))
        self.assertFalse((self.base / "autopilot/lock").exists())

    def test_published_closed_and_owner_hold_keep_original_source(self):
        for fixture in ({"billing": {"posted_at": "historical"}}, {"billing": {}, "closed_at": "historical"}):
            with self.assertRaises(RuntimeError):
                self.activate(fixture)
            self.assertEqual(self.code.read_bytes(), self.original_code)
            self.assertEqual(self.state.read_bytes(), self.original_state)
        (self.base / "OWNER_HOLD").touch()
        with self.assertRaisesRegex(RuntimeError, "OWNER_HOLD"):
            self.activate({"billing": {}})
        self.assertEqual(self.code.read_bytes(), self.original_code)


if __name__ == "__main__":
    unittest.main()
