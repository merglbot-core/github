"""Optional, single-target Codex heartbeat cadence/stop adapter. Never enables."""
import json
import hashlib
import os
from pathlib import Path
import re
import sqlite3
import tempfile
import legacy_source

FIELDS = {"version", "id", "kind", "name", "prompt", "status", "rrule",
          "target_thread_id", "created_at", "updated_at"}


def parse(text):
    values, lines = {}, {}
    for index, line in enumerate(text.splitlines()):
        if not line.strip():
            continue
        match = re.fullmatch(r"([a-z_]+)\s*=\s*(.+)", line)
        if not match or match[1] not in FIELDS or match[1] in values:
            raise ValueError("unsupported_toml")
        value = json.loads(match[2])
        if type(value) not in (str, int, bool):
            raise ValueError("unsupported_scalar")
        values[match[1]], lines[match[1]] = value, index
    if set(values) != FIELDS or any(type(values[k]) is not int for k in ("version", "created_at", "updated_at")):
        raise ValueError("invalid_fields")
    if any(not isinstance(values[k], str) for k in FIELDS - {"version", "created_at", "updated_at"}):
        raise ValueError("invalid_strings")
    if values["status"] not in ("ACTIVE", "PAUSED"):
        raise ValueError("invalid_status")
    return values, lines


def read_db(path, identity):
    connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=3)
    try:
        connection.execute("PRAGMA query_only = ON")
        rows = connection.execute("SELECT id, target_thread_id, status, rrule, next_run_at, updated_at "
                                  "FROM automations WHERE id = ?", (identity,)).fetchall()
        if len(rows) != 1:
            raise ValueError("missing_db_target")
        return dict(zip(("id", "target_thread_id", "status", "rrule", "next_run_at", "updated_at"), rows[0]))
    finally:
        connection.close()


def replace(path, text):
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".pilot-heartbeat-")
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def sync(state_dir, active, terminal, now, home=None):
    config_path = state_dir / "heartbeat.json"
    if not config_path.exists():
        return {"status": "not_configured"}
    try:
        config = json.loads(config_path.read_text())
        if not isinstance(config, dict) or set(config) != {"id", "target_thread_id"} or any(
                not isinstance(v, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,128}", v) for v in config.values()):
            raise ValueError("invalid_config")
        root = (home or Path.home()) / ".codex"
        path = root / "automations" / config["id"] / "automation.toml"
        if path.resolve() != path.absolute() or not path.is_file():
            raise ValueError("unsafe_target_path")
        original = path.read_text()
        values, lines = parse(original)
        db_path = root / "sqlite/codex-dev.db"
        db = read_db(db_path, config["id"])
        if any(values[k] != config[k] or db[k] != config[k] for k in config):
            raise ValueError("target_mismatch")
        if db["status"] not in ("ACTIVE", "PAUSED") or type(db["updated_at"]) is not int:
            raise ValueError("invalid_db_state")
        desired = {"rrule": "FREQ=MINUTELY;INTERVAL=" + ("5" if active else "15"),
                   "status": "PAUSED" if terminal or "PAUSED" in (values["status"], db["status"]) else "ACTIVE"}
        changed = any(values[k] != v for k, v in desired.items())
        needs_sync = any(db[k] != v for k, v in desired.items()) and db["updated_at"] >= values["updated_at"]
        if changed or needs_sync:
            desired["updated_at"] = max(int(now.timestamp() * 1000), values["updated_at"] + 1, db["updated_at"] + 1)
            output = original.splitlines(keepends=True)
            for key, value in desired.items():
                output[lines[key]] = key + " = " + json.dumps(value, ensure_ascii=False) + "\n"
            if path.read_text() != original:
                raise ValueError("file_race")
            replace(path, "".join(output))
        actual, _ = parse(path.read_text())
        db = read_db(db_path, config["id"])
        if (any(actual[k] != config[k] or db[k] != config[k] for k in config)
                or any(actual[k] != desired[k] for k in ("status", "rrule"))
                or db["status"] not in ("ACTIVE", "PAUSED") or not isinstance(db["rrule"], str)):
            raise ValueError("readback_invalid")
        if any(db[k] != desired[k] for k in ("status", "rrule")) or (terminal and db["next_run_at"] is not None):
            if actual["status"] == "PAUSED" and actual["kind"] == "heartbeat" and actual["version"] == 1:
                proof = legacy_source.verify(db_path, config)
                if proof["status"] == "verified":
                    final_text = path.read_text()
                    final, _ = parse(final_text)
                    if (any(final[k] != desired[k] for k in ("status", "rrule"))
                            or any(final[k] != actual[k] for k in ("kind", "version"))
                            or any(final[k] != config[k] for k in config)):
                        raise ValueError("file_race")
                    proof.update(file_sha256=hashlib.sha256(final_text.encode()).hexdigest(), **config)
                    return {"status": "verified", "paused": True, "proof": proof, "database_cache": "stale"}
                return {"status": "pending", "reason": "app_sync", "source_proof": proof}
            return {"status": "pending", "reason": "app_sync"}
        return {"status": "verified", "paused": actual["status"] == "PAUSED"}
    except (OSError, ValueError, TypeError, KeyError, sqlite3.Error):
        return {"status": "unverified"}
