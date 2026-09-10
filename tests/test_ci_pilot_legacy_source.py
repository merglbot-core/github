from contextlib import closing
import hashlib
import json
import os
import plistlib
import sqlite3
import struct
import subprocess
import unittest
from unittest.mock import patch
import test_ci_pilot_heartbeat as fixtures
NOW, h = fixtures.NOW, fixtures.h


class LegacySourceTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.HeartbeatTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.f.values.update(kind="heartbeat", status="PAUSED", rrule="FREQ=MINUTELY;INTERVAL=15")
        self.f.write_file()
        alias = self.f.home / ".codex-o2"
        alias.mkdir()
        (alias / "sqlite").symlink_to(self.f.db.parent)
        (alias / "automations").symlink_to(self.f.path.parent.parent)
        with closing(sqlite3.connect(str(self.f.db), isolation_level=None)) as db:
            for column in ("account_id", "user_id", "installation_id", "legacy_automation_id"):
                db.execute("ALTER TABLE automations ADD COLUMN " + column + " TEXT")
            db.execute("UPDATE automations SET rrule='FREQ=MINUTELY;INTERVAL=5'")
        self.app = self.f.home / "Codex.app"
        self.info = self.app / "Contents/Info.plist"
        self.archive = self.app / "Contents/Resources/app.asar"
        self.archive.parent.mkdir(parents=True)
        self.info.write_bytes(plistlib.dumps({"CFBundleShortVersionString": h.legacy_source.VERSION,
                                            "CFBundleVersion": h.legacy_source.BUILD, "CFBundleExecutable": "ChatGPT"}))
        modules = {name: ("audited fixture " + name).encode() for name in h.legacy_source.HASHES}
        entries, body = {}, b""
        for name, content in modules.items():
            entries[name] = {"offset": str(len(body)), "size": len(content)}
            body += content
        header = json.dumps({"files": {".vite": {"files": {"build": {"files": entries}}}}}).encode()
        self.archive.write_bytes(struct.pack("<4I", 4, len(header) + 8, len(header) + 4, len(header)) + header + body)
        for path in (self.info, self.archive):
            os.utime(path, (NOW.timestamp() - 60, NOW.timestamp() - 60))
        patches = [patch.object(h.legacy_source, "APP", self.app),
                   patch.object(h.legacy_source.Path, "home", return_value=self.f.home),
                   patch.object(h.legacy_source, "HASHES", {name: hashlib.sha256(data).hexdigest() for name, data in modules.items()}),
                   patch.object(h.legacy_source.subprocess, "run", side_effect=self.process)]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)

    def process(self, args, **kwargs):
        output = ("30346 Thu Sep 10 00:00:00 2026 " + str(self.app / "Contents/MacOS/ChatGPT") + "\n"
                  if args[0] == "ps" else "p30346\nn" + str(self.f.db) + "\n")
        return subprocess.CompletedProcess(args, 0, output, "")

    def test_paused_legacy_cache_gap_proves_only_future_admission_stopped(self):
        before = self.f.db.read_bytes()
        proof = self.f.sync(terminal=True)
        self.assertEqual(proof["status"], "verified")
        self.assertEqual(proof["database_cache"], "stale")
        self.assertEqual(proof["proof"]["paused_file_admission"], "disabled")
        self.assertEqual(proof["proof"]["already_admitted"], "not_cancelled")
        self.assertEqual(proof["proof"]["id"], self.f.config["id"])
        self.assertEqual(self.f.db.read_bytes(), before)
        with closing(sqlite3.connect(str(self.f.db), isolation_level=None)) as db:
            db.execute("UPDATE automations SET status='ACTIVE', next_run_at=999")
        self.assertEqual(self.f.sync(terminal=True)["status"], "verified")

    def test_scoped_or_migrated_successor_prevents_file_proof(self):
        with closing(sqlite3.connect(str(self.f.db), isolation_level=None)) as db:
            db.execute("INSERT INTO automations (id,target_thread_id,account_id) VALUES ('successor',?,'scope')",
                       (self.f.config["target_thread_id"],))
        self.assertEqual(self.f.sync(terminal=True)["source_proof"]["reason"], "scoped_successor")
        with closing(sqlite3.connect(str(self.f.db), isolation_level=None)) as db:
            db.execute("UPDATE automations SET target_thread_id='other',legacy_automation_id=? WHERE id='successor'",
                       (self.f.config["id"],))
        self.assertEqual(self.f.sync(terminal=True)["status"], "pending")
        with closing(sqlite3.connect(str(self.f.db), isolation_level=None)) as db:
            db.execute("DELETE FROM automations WHERE id='successor'")
            db.execute("UPDATE automations SET account_id='scope'")
        self.assertEqual(self.f.sync(terminal=True)["source_proof"]["reason"], "not_legacy_identity")

    def test_changed_hash_or_unreadable_source_never_proves_stop(self):
        with patch.dict(h.legacy_source.HASHES, {next(iter(h.legacy_source.HASHES)): "0" * 64}):
            self.assertEqual(self.f.sync(terminal=True)["source_proof"]["reason"], "app_hash_unknown")
        self.archive.write_bytes(b"invalid")
        self.assertEqual(self.f.sync(terminal=True)["status"], "pending")

    def test_unknown_running_version_or_home_fails_closed(self):
        with patch.object(h.legacy_source.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "", "")):
            self.assertEqual(self.f.sync(terminal=True)["source_proof"]["reason"], "running_version_unknown")
        def wrong_home(args, **kwargs):
            return self.process(args, **kwargs) if args[0] == "ps" else subprocess.CompletedProcess(args, 0, "p30346\n", "")
        with patch.object(h.legacy_source.subprocess, "run", side_effect=wrong_home):
            self.assertEqual(self.f.sync(terminal=True)["source_proof"]["reason"], "runtime_home_unknown")

    def test_active_file_still_requires_database_sync(self):
        self.f.values["status"] = "ACTIVE"
        self.f.write_file()
        with closing(sqlite3.connect(str(self.f.db), isolation_level=None)) as db:
            db.execute("UPDATE automations SET status='ACTIVE'")
        self.assertEqual(self.f.sync()["status"], "pending")

    def test_final_process_identity_change_invalidates_proof(self):
        with patch.object(h.legacy_source, "runtime", side_effect=[("1", 100), ("2", 100)]):
            self.assertEqual(self.f.sync(terminal=True)["source_proof"]["reason"], "runtime_changed")

    def test_different_home_alias_mapping_is_not_supported(self):
        alias = self.f.home / ".codex-o2/automations"
        alias.unlink()
        target = alias / self.f.config["id"] / "automation.toml"
        target.parent.mkdir(parents=True)
        target.write_bytes(self.f.path.read_bytes())
        self.assertEqual(self.f.sync(terminal=True)["source_proof"]["reason"], "home_alias_mapping_changed")

    def test_cadence_changed_during_source_probe_is_not_verified(self):
        def concurrent_change(*args):
            self.f.values["rrule"] = "FREQ=MINUTELY;INTERVAL=30"
            self.f.write_file()
            return {"status": "verified"}
        with patch.object(h.legacy_source, "verify", side_effect=concurrent_change):
            self.assertEqual(self.f.sync(terminal=True)["status"], "unverified")


if __name__ == "__main__":
    unittest.main()
