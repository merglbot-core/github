import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).parent


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


installer = load("numeric_installer", HERE / "install.py")
shared = load("shared_installer", HERE.parent / "gh-cost-922-window/install.py")
patcher = load("numeric_patcher", HERE / "patch_autopilot.py")


class InstallationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        (self.base / "autopilot").mkdir()
        self.code = self.base / "autopilot.py"
        self.state = self.base / "state.json"
        self.old_code = (HERE / "fixture_post_billing.py").read_bytes()
        self.code.write_bytes(self.old_code)
        for name, value in (("BASE", self.base), ("HOLD", self.base / "OWNER_HOLD")):
            context = patch.object(shared, name, value)
            context.start()
            self.addCleanup(context.stop)
        context = patch.object(installer, "load", side_effect=lambda name, path:
                               shared if name == "window_install" else patcher)
        context.start()
        self.addCleanup(context.stop)

    def run_install(self, acceptance_markers, dry=False, closed_at=None):
        # This fixture accepts lifecycle sentinels only, never financial/customer
        # data. CodeQL previously mistook the parameter named "billing" for it.
        self.assertLessEqual(set(acceptance_markers), {"posted_at", "closed_at"})
        self.assertTrue(all(value in (None, "historical acceptance")
                            for value in acceptance_markers.values()))
        original = {"billing": acceptance_markers, "dod": {"natural": "keep"}, "subs": {"921": "owner"}}
        if closed_at:
            original["closed_at"] = closed_at
        self.old_state = json.dumps(original).encode()
        self.state.write_bytes(self.old_state)
        return installer.install(shared.digest(self.old_code), shared.digest(self.old_state), dry)

    def test_published_and_closed_acceptance_reject_without_any_replacement(self):
        for key in ("posted_at", "closed_at"):
            for dry in (False, True):
                with self.subTest(key=key, dry=dry):
                    with self.assertRaisesRegex(RuntimeError, "requires reconciliation"):
                        self.run_install({key: "historical acceptance"}, dry)
                    self.assertEqual(self.code.read_bytes(), self.old_code)
                    self.assertEqual(self.state.read_bytes(), self.old_state)
                    self.assertFalse((self.base / "backups").exists())
                    self.assertFalse((self.base / "autopilot/lock").exists())

    def test_top_level_actual_epic_closure_marker_rejects_install(self):
        with self.assertRaisesRegex(RuntimeError, "requires reconciliation"):
            self.run_install({}, closed_at="historical closure")
        self.assertEqual(self.code.read_bytes(), self.old_code)
        self.assertEqual(self.state.read_bytes(), self.old_state)
        self.assertFalse((self.base / "backups").exists())

    def test_unpublished_install_replaces_only_source_and_retains_backup(self):
        result = self.run_install({"posted_at": None, "closed_at": None})
        self.assertEqual(self.code.read_text(), patcher.patch(self.old_code.decode()))
        self.assertEqual(self.state.read_bytes(), self.old_state)
        self.assertEqual((Path(result["backup"]) / "autopilot.py").read_bytes(), self.old_code)
        self.assertFalse((self.base / "autopilot/lock").exists())

    def test_unpublished_dry_run_preserves_source_and_state(self):
        result = self.run_install({}, True)
        self.assertTrue(result["dry_run"])
        self.assertEqual(self.code.read_bytes(), self.old_code)
        self.assertEqual(self.state.read_bytes(), self.old_state)
        self.assertFalse((self.base / "backups").exists())


if __name__ == "__main__":
    unittest.main()
