"""Install the reviewed acceptance fix into the local #910 autopilot under its lock.

This script never edits state.json. Give the freshly reviewed autopilot SHA via
--expected-sha; a concurrent tick, changed source or OWNER_HOLD stops the install.
"""

import argparse
import datetime as dt
import hashlib
import importlib.util
import json
import os
import stat
from pathlib import Path
import tempfile


HERE = Path(__file__).resolve().parent
BASE = Path.home() / ".merglbot/gh-cost-910"
AUTO = BASE / "autopilot"
CODE = BASE / "autopilot.py"
MODULE = BASE / "cost_semantic.py"
STATE = BASE / "state.json"
HOLD = Path.home() / ".claude/merglbot-preauth/OWNER_HOLD"


def digest(data):
    return hashlib.sha256(data).hexdigest()


def write_atomic(path, data):
    mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o600
    fd, name = tempfile.mkstemp(prefix=".cost-acceptance-", dir=path.parent)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def acquire_lock():
    AUTO.mkdir(parents=True, exist_ok=True)
    (AUTO / "lock").mkdir()  # FileExistsError means a live tick owns the state.


def release_lock():
    (AUTO / "lock").rmdir()


def load_patch():
    spec = importlib.util.spec_from_file_location("cost_patch", HERE / "patch_autopilot.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.patch_both


def install(expected_sha, dry_run):
    if HOLD.exists():
        raise RuntimeError("OWNER_HOLD is present")
    acquire_lock()
    try:
        old_code = CODE.read_bytes()
        old_module = MODULE.read_bytes() if MODULE.exists() else None
        old_state = STATE.read_bytes()
        if digest(old_code) != expected_sha:
            raise RuntimeError("autopilot source drift; no installation")
        new_code = load_patch()(old_code.decode()).encode()
        new_module = (HERE / "semantic.py").read_bytes()
        compile(new_code, str(CODE), "exec")
        compile(new_module, str(MODULE), "exec")
        facts = {"source_before_sha256": digest(old_code),
                 "source_after_sha256": digest(new_code),
                 "module_before_present": old_module is not None,
                 "module_before_sha256": digest(old_module) if old_module is not None else None,
                 "module_after_sha256": digest(new_module),
                 "state_sha256": digest(old_state)}
        if dry_run:
            return {"dry_run": True, **facts}
        backup = BASE / "backups" / ("acceptance-" + dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
        backup.mkdir(parents=True, mode=0o700)
        write_atomic(backup / "autopilot.py", old_code)
        write_atomic(backup / "state.json", old_state)
        if old_module is not None:
            write_atomic(backup / "cost_semantic.py", old_module)
        write_atomic(backup / "manifest.json", (json.dumps(facts, indent=2) + "\n").encode())
        try:
            write_atomic(MODULE, new_module)
            write_atomic(CODE, new_code)
        except Exception:
            write_atomic(CODE, old_code)
            if old_module is None:
                MODULE.unlink(missing_ok=True)
            else:
                write_atomic(MODULE, old_module)
            raise
        return {"backup": str(backup), "state_unchanged": digest(STATE.read_bytes()) == digest(old_state), **facts}
    finally:
        release_lock()


def rollback(backup):
    if HOLD.exists():
        raise RuntimeError("OWNER_HOLD is present")
    acquire_lock()
    try:
        manifest = json.loads((backup / "manifest.json").read_text())
        if digest(CODE.read_bytes()) != manifest["source_after_sha256"]:
            raise RuntimeError("newer autopilot source exists; no rollback")
        if not MODULE.exists() or digest(MODULE.read_bytes()) != manifest["module_after_sha256"]:
            raise RuntimeError("newer semantic module exists; no rollback")
        old_code = (backup / "autopilot.py").read_bytes()
        old_module = backup / "cost_semantic.py"
        module_was_present = manifest["module_before_present"]
        if old_module.exists() != module_was_present:
            raise RuntimeError("backup module presence drift; no rollback")
        old_module_bytes = old_module.read_bytes() if module_was_present else None
        if digest(old_code) != manifest["source_before_sha256"] or (
                module_was_present and digest(old_module_bytes) != manifest["module_before_sha256"]):
            raise RuntimeError("backup digest mismatch; no rollback")
        write_atomic(CODE, old_code)
        if module_was_present:
            write_atomic(MODULE, old_module_bytes)
        else:
            MODULE.unlink(missing_ok=True)
        return {"restored_source_sha256": digest(CODE.read_bytes()), "state_unchanged": True}
    finally:
        release_lock()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-sha")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--rollback", type=Path)
    args = parser.parse_args()
    if args.rollback:
        result = rollback(args.rollback)
    elif args.expected_sha:
        result = install(args.expected_sha, args.dry_run)
    else:
        parser.error("--expected-sha or --rollback is required")
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
