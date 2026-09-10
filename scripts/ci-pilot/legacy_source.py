"""Read-only proof of the audited legacy file scheduler's admission rule."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import sqlite3
import struct
import subprocess

APP = Path("/Applications/Codex.app")
VERSION, BUILD = "26.903.61454", "8378"
HASHES = {"src-J2PvP4xj.js": "4cc980cd737b02f999b9fe8d9757c37d2ce86c928043f19f46d56cc52bce8f66",
          "main-D87AK7lw.js": "440f7b699361ec30aa29e9517055e06d85f5e2da00a58a1ff4673e3c4cb0628f"}


class SourceGap(Exception):
    pass


def known_homes(db_path, config):
    home = Path.home()
    canonical = home / ".codex"
    target = canonical / "automations" / config["id"] / "automation.toml"
    for alias in (".codex", ".codex-o2"):
        root = home / alias
        if (not os.path.samefile(root / "sqlite/codex-dev.db", db_path)
                or not os.path.samefile(root / "automations" / config["id"] / "automation.toml", target)):
            raise SourceGap("home_alias_mapping_changed")


def scope(db_path, config):
    db = sqlite3.connect(db_path.as_uri() + "?mode=ro", uri=True, timeout=3)
    try:
        db.execute("PRAGMA query_only = ON")
        row = db.execute("SELECT target_thread_id, account_id, user_id, installation_id, legacy_automation_id "
                         "FROM automations WHERE id=?", (config["id"],)).fetchone()
        if row is None or row[0] != config["target_thread_id"] or any(v is not None for v in row[1:]):
            raise SourceGap("not_legacy_identity")
        successors = db.execute("SELECT COUNT(*) FROM automations WHERE id != ? "
                                "AND (legacy_automation_id=? OR target_thread_id=?) "
                                "AND (account_id IS NOT NULL OR user_id IS NOT NULL OR installation_id IS NOT NULL "
                                "OR legacy_automation_id IS NOT NULL)",
                                (config["id"], config["id"], config["target_thread_id"])).fetchone()[0]
        if successors:
            raise SourceGap("scoped_successor")
    finally:
        db.close()


def installed(app):
    info_path, archive = app / "Contents/Info.plist", app / "Contents/Resources/app.asar"
    info = plistlib.loads(info_path.read_bytes())
    if (info.get("CFBundleShortVersionString"), info.get("CFBundleVersion"), info.get("CFBundleExecutable")) != (VERSION, BUILD, "ChatGPT"):
        raise SourceGap("app_version_unknown")
    before = archive.stat()
    with archive.open("rb") as stream:
        words = struct.unpack("<4I", stream.read(16))
        if words[0] != 4 or not 0 < words[3] <= words[1] <= 4_000_000:
            raise SourceGap("asar_header_invalid")
        header = json.loads(stream.read(words[3]))
        entries = header["files"][".vite"]["files"]["build"]["files"]
        for name, expected in HASHES.items():
            entry = entries[name]
            size, offset = entry["size"], int(entry["offset"])
            if not 0 < size <= 5_000_000 or offset < 0 or 8 + words[1] + offset + size > before.st_size:
                raise SourceGap("asar_entry_invalid")
            stream.seek(8 + words[1] + offset)
            if hashlib.sha256(stream.read(size)).hexdigest() != expected:
                raise SourceGap("app_hash_unknown")
    after = archive.stat()
    if (after.st_ino, after.st_size, after.st_mtime_ns) != (before.st_ino, before.st_size, before.st_mtime_ns):
        raise SourceGap("app_changed")
    return max(before.st_mtime, info_path.stat().st_mtime)


def runtime(app, db_path, artifact_time):
    result = subprocess.run(["ps", "-axo", "pid=,lstart=,comm="], capture_output=True, text=True,
                            timeout=10, env={**os.environ, "TZ": "UTC", "LC_ALL": "C"}, check=True)
    executable, matches = str(app / "Contents/MacOS/ChatGPT"), []
    for line in result.stdout.splitlines():
        match = re.fullmatch(r"\s*(\d+)\s+(\w{3}\s+\w{3}\s+\d+\s+\d\d:\d\d:\d\d\s+\d{4})\s+(.+)", line)
        if match and match[3] == executable:
            start = datetime.strptime(match[2], "%a %b %d %H:%M:%S %Y").replace(tzinfo=timezone.utc).timestamp()
            matches.append((match[1], start))
    if len(matches) != 1 or matches[0][1] < artifact_time:
        raise SourceGap("running_version_unknown")
    result = subprocess.run(["/usr/sbin/lsof", "-a", "-p", matches[0][0], "-Fn", "--", str(db_path)],
                            capture_output=True, text=True, timeout=10, check=True)
    if not any(line.startswith("n") and os.path.samefile(line[1:], db_path) for line in result.stdout.splitlines()):
        raise SourceGap("runtime_home_unknown")
    return matches[0]


def verify(db_path, config, app=None):
    try:
        app = app or APP
        known_homes(db_path, config)
        scope(db_path, config)
        artifact_time = installed(app)
        process = runtime(app, db_path, artifact_time)
        scope(db_path, config)
        if installed(app) != artifact_time or runtime(app, db_path, artifact_time) != process:
            raise SourceGap("runtime_changed")
        known_homes(db_path, config)
        return {"status": "verified", "source": "legacy_file", "app_version": VERSION, "build": BUILD,
                "module_sha256": HASHES.copy(), "paused_file_admission": "disabled",
                "known_home_aliases": [".codex", ".codex-o2"],
                "already_admitted": "not_cancelled"}
    except Exception as error:
        return {"status": "unverified", "reason": str(error) if isinstance(error, SourceGap) else "source_read_gap"}
