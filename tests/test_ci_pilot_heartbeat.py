from contextlib import closing
import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

spec = importlib.util.spec_from_file_location("heartbeat", Path(__file__).resolve().parents[1] / "scripts/ci-pilot/heartbeat.py")
h = importlib.util.module_from_spec(spec)
spec.loader.exec_module(h)
from datetime import datetime, timezone
NOW = datetime(2026, 9, 10, tzinfo=timezone.utc)


class HeartbeatTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name).resolve()
        self.state = self.home / "state"
        self.state.mkdir()
        self.config = {"id": "existing-pilot", "target_thread_id": "thread-123"}
        (self.state / "heartbeat.json").write_text(json.dumps(self.config))
        self.path = self.home / ".codex/automations/existing-pilot/automation.toml"
        self.path.parent.mkdir(parents=True)
        self.values = {"version": 1, **self.config, "kind": "task", "name": "original",
                       "prompt": "Keep every byte\nincluding quotes: \"x\"", "status": "PAUSED",
                       "rrule": "FREQ=MINUTELY;INTERVAL=15", "created_at": 1, "updated_at": 2}
        self.write_file()
        self.db = self.home / ".codex/sqlite/codex-dev.db"
        self.db.parent.mkdir()
        with closing(sqlite3.connect(str(self.db), isolation_level=None)) as db:
            db.execute("CREATE TABLE automations (id TEXT PRIMARY KEY, target_thread_id TEXT, status TEXT, rrule TEXT, next_run_at INTEGER, updated_at INTEGER)")
            db.execute("INSERT INTO automations VALUES (?, ?, ?, ?, NULL, ?)",
                       tuple(self.values[k] for k in ("id", "target_thread_id", "status", "rrule", "updated_at")))

    def write_file(self):
        self.path.write_text("".join(k + " = " + json.dumps(v) + "\n" for k, v in self.values.items()))

    def app_sync(self):
        data, _ = h.parse(self.path.read_text())
        with closing(sqlite3.connect(str(self.db), isolation_level=None)) as db:
            db.execute("UPDATE automations SET status=?, rrule=?, updated_at=?, next_run_at=NULL WHERE id=?",
                       tuple(data[k] for k in ("status", "rrule", "updated_at", "id")))

    def sync(self, active=False, terminal=False):
        return h.sync(self.state, active, terminal, NOW, self.home)

    def test_cadence_preserves_pause_prompt_and_waits_for_database(self):
        original = self.values.copy()
        self.assertEqual(self.sync(active=True)["status"], "unverified")
        changed, _ = h.parse(self.path.read_text())
        self.assertEqual(changed["rrule"], "FREQ=MINUTELY;INTERVAL=5")
        self.assertGreater(changed["updated_at"], original["updated_at"])
        for key in original.keys() - {"rrule", "updated_at"}:
            self.assertEqual(changed[key], original[key])
        pending_bytes = self.path.read_bytes()
        self.assertEqual(self.sync(active=True)["status"], "unverified")
        self.assertEqual(self.path.read_bytes(), pending_bytes)
        self.app_sync()
        self.assertEqual(self.sync(active=True), {"status": "verified", "paused": True})
        self.assertEqual(self.sync()["status"], "unverified")
        self.app_sync()
        self.assertEqual(self.sync()["status"], "verified")

    def test_terminal_pauses_active_heartbeat_and_checks_next_run(self):
        self.values["status"] = "ACTIVE"
        self.write_file()
        self.app_sync()
        self.assertEqual(self.sync(terminal=True)["status"], "unverified")
        self.assertEqual(h.parse(self.path.read_text())[0]["status"], "PAUSED")
        self.app_sync()
        with closing(sqlite3.connect(str(self.db), isolation_level=None)) as db:
            db.execute("UPDATE automations SET next_run_at=999")
        self.assertEqual(self.sync(terminal=True)["status"], "unverified")
        self.app_sync()
        self.assertEqual(self.sync(terminal=True), {"status": "verified", "paused": True})

    def test_target_mismatch_or_unknown_syntax_never_writes(self):
        original = self.path.read_text()
        for text in (original + "unknown = 1\n", original.replace('name = "original"', 'name = """multiline"""'),
                     original.replace('target_thread_id = "thread-123"', 'target_thread_id = "other"')):
            self.path.write_text(text)
            self.assertEqual(self.sync(active=True)["status"], "unverified")
            self.assertEqual(self.path.read_text(), text)
        self.path.write_text(original)
        with closing(sqlite3.connect(str(self.db), isolation_level=None)) as db:
            db.execute("UPDATE automations SET target_thread_id='other'")
        self.assertEqual(self.sync(active=True)["status"], "unverified")
        self.assertEqual(self.path.read_text(), original)

    def test_missing_db_no_target_creation_and_optional_config(self):
        self.db.unlink()
        original = self.path.read_bytes()
        self.assertEqual(self.sync(active=True)["status"], "unverified")
        self.assertEqual(self.path.read_bytes(), original)
        self.assertFalse(self.db.exists())
        self.path.unlink()
        self.assertEqual(self.sync()["status"], "unverified")
        self.assertFalse(self.path.exists())
        (self.state / "heartbeat.json").unlink()
        self.assertEqual(self.sync(), {"status": "not_configured"})

    def test_database_pause_cannot_be_enabled_by_stale_active_file(self):
        self.values["status"] = "ACTIVE"
        self.write_file()  # DB still proves PAUSED.
        self.assertEqual(self.sync(active=True)["status"], "unverified")
        self.assertEqual(h.parse(self.path.read_text())[0]["status"], "PAUSED")

    def test_invalid_id_and_symlink_cannot_write_other_automation(self):
        original = self.path.read_bytes()
        (self.state / "heartbeat.json").write_text(json.dumps({**self.config, "id": "../other"}))
        self.assertEqual(self.sync(active=True)["status"], "unverified")
        self.assertEqual(self.path.read_bytes(), original)
        (self.state / "heartbeat.json").write_text(json.dumps(self.config))
        other = self.home / "unrelated.toml"
        self.path.rename(other)
        self.path.symlink_to(other)
        self.assertEqual(self.sync(active=True)["status"], "unverified")
        self.assertEqual(other.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
