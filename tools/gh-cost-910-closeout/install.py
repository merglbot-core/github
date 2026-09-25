"""Install the #910 closeout guard and reopen its #921 state under the existing lock."""

import argparse
import datetime as dt
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import tempfile

HERE = Path(__file__).resolve().parent
BASE = Path.home() / ".merglbot/gh-cost-910"
LOCK = BASE / "autopilot/lock"
CODE = BASE / "autopilot.py"
STATE = BASE / "state.json"
HOLD = Path.home() / ".claude/merglbot-preauth/OWNER_HOLD"


def digest(data):
    return hashlib.sha256(data).hexdigest()


def write_atomic(path, data):
    mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o600
    fd, temp = tempfile.mkstemp(prefix=".closeout-", dir=path.parent)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def load_patch():
    spec = importlib.util.spec_from_file_location("closeout_patch", HERE / "patch_autopilot.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.patch


def change_state(raw):
    state = json.loads(raw)
    record = state["subs"]["921"]
    if record.get("technical_hold") or not record.get("board_done_at"):
        raise RuntimeError("#921 migration state drift")
    if state.get("closed_at"):
        raise RuntimeError("EPIC already marked closed")
    record["board_done_at"] = None
    record["technical_hold"] = True
    return (json.dumps(state, ensure_ascii=False, indent=2) + "\n").encode()


def install(expected_code, expected_state, dry_run):
    if HOLD.exists():
        raise RuntimeError("OWNER_HOLD is present")
    LOCK.mkdir()
    try:
        old_code, old_state = CODE.read_bytes(), STATE.read_bytes()
        if digest(old_code) != expected_code or digest(old_state) != expected_state:
            raise RuntimeError("autopilot source or state drift")
        new_code = load_patch()(old_code.decode()).encode()
        new_state = change_state(old_state)
        compile(new_code, str(CODE), "exec")
        facts = {"code_before_sha256": digest(old_code), "code_after_sha256": digest(new_code),
                 "state_before_sha256": digest(old_state), "state_after_sha256": digest(new_state)}
        if dry_run:
            return {"dry_run": True, **facts}
        backup = BASE / "backups" / ("closeout-" + dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
        backup.mkdir(parents=True, mode=0o700)
        write_atomic(backup / "autopilot.py", old_code)
        write_atomic(backup / "state.json", old_state)
        write_atomic(backup / "manifest.json", (json.dumps(facts, indent=2) + "\n").encode())
        try:
            write_atomic(CODE, new_code)
            write_atomic(STATE, new_state)
        except Exception:
            if digest(STATE.read_bytes()) == digest(old_state):
                write_atomic(CODE, old_code)
            raise
        if digest(CODE.read_bytes()) != facts["code_after_sha256"] or digest(STATE.read_bytes()) != facts["state_after_sha256"]:
            raise RuntimeError("installation readback mismatch")
        return {"backup": str(backup), **facts}
    finally:
        LOCK.rmdir()


def rollback(backup):
    if HOLD.exists():
        raise RuntimeError("OWNER_HOLD is present")
    LOCK.mkdir()
    try:
        facts = json.loads((backup / "manifest.json").read_text())
        old_code, old_state = (backup / "autopilot.py").read_bytes(), (backup / "state.json").read_bytes()
        if digest(old_code) != facts["code_before_sha256"] or digest(old_state) != facts["state_before_sha256"]:
            raise RuntimeError("backup corrupt")
        if digest(CODE.read_bytes()) != facts["code_after_sha256"] or digest(STATE.read_bytes()) != facts["state_after_sha256"]:
            raise RuntimeError("newer code or state exists; rebase rollback before restoring")
        current = json.loads(STATE.read_bytes())
        # The legacy code ignores this hold and can close #921/EPIC from its
        # historical Done mark. Never restore it while the hold is active.
        if (current.get("subs", {}).get("921") or {}).get("technical_hold"):
            raise RuntimeError("unsafe legacy rollback: #921 technical hold is active")
        write_atomic(CODE, old_code)
        write_atomic(STATE, old_state)
        return {"restored_code_sha256": digest(CODE.read_bytes()),
                "restored_state_sha256": digest(STATE.read_bytes())}
    finally:
        LOCK.rmdir()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-code")
    parser.add_argument("--expected-state")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--rollback", type=Path)
    args = parser.parse_args()
    if args.rollback:
        result = rollback(args.rollback)
    elif args.expected_code and args.expected_state:
        result = install(args.expected_code, args.expected_state, args.dry_run)
    else:
        parser.error("--expected-code and --expected-state, or --rollback, are required")
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
