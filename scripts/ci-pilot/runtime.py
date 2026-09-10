#!/usr/bin/env python3
"""One launchd wakeup; no model, daemon loop or runtime installation."""
import argparse
import datetime as dt
import fcntl
import json
import os
from pathlib import Path
import plistlib
import signal
import subprocess
import sys

from controller import DEADLINE, GitHub, atomic, cleanup, instant

LABEL = "ai.merglbot.ci-pilot-supervisor"


def schedule(result, now):
    terminal = (result.get("action") == "cleanup_verified"
                and result.get("reason") in ("deadline", "case_limit"))
    history = result.get("history") or {}
    active = result.get("status") in ("active", "unverified") or history.get("unfinished_runs", 0) > 0
    return {"stopped": terminal,
            "next_due": None if terminal else (now + dt.timedelta(seconds=300 if active else 900)).isoformat(),
            "admitted_runs": "DATA_GAP" if (result.get("status") == "unverified"
                or "unfinished_runs" not in history or "data_gaps" not in history
                or history["unfinished_runs"] or history["data_gaps"]) else "observed_complete"}


def emergency_cleanup(state_dir):
    """A separate bounded subprocess avoids repeating the stalled snapshot path."""
    try:
        process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "cleanup",
                                    "--state-dir", str(state_dir)], stdout=subprocess.PIPE,
                                   stderr=subprocess.DEVNULL, text=True, start_new_session=True)
        try:
            output, _ = process.communicate(timeout=240)
        except subprocess.TimeoutExpired:
            kill_group(process)
            raise
        if process.returncode == 0:
            return json.loads(output)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        pass
    return {"action": "cleanup_required", "status": "unverified", "reason": "emergency_cleanup_gap"}


def kill_group(process):
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait()


def run_controller(state_dir):
    process = subprocess.Popen([sys.executable, str(Path(__file__).with_name("controller.py")),
                                "tick", "--state-dir", str(state_dir), "--apply"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    try:
        process.wait(timeout=240)
    except subprocess.TimeoutExpired:
        # Stop outstanding gh children before cleanup/readback can establish absence.
        kill_group(process)
        raise


def locked_cleanup(state_dir):
    state_dir.mkdir(parents=True, exist_ok=True)
    with (state_dir / "controller.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        return cleanup(GitHub(), True)


def unload():
    return subprocess.run(["/bin/launchctl", "bootout", f"gui/{os.getuid()}/{LABEL}"],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20).returncode


def wake(state_dir, now):
    plan_path = state_dir / "runtime.json"
    invalid_state = False
    try:
        plan = json.loads(plan_path.read_text()) if plan_path.exists() else {}
        state = json.loads((state_dir / "state.json").read_text()) if (state_dir / "state.json").exists() else {}
        if not isinstance(plan, dict) or not isinstance(state, dict):
            raise ValueError("invalid_state_shape")
        if type(plan.get("stopped", False)) is not bool:
            raise ValueError("invalid_stopped_shape")
        if not isinstance(state.get("counted_prs", []), list):
            raise ValueError("invalid_counter_shape")
        if plan.get("next_due"):
            instant(plan["next_due"])
    except (ValueError, OSError, TypeError, AttributeError):
        plan, state = {}, {}
        invalid_state = True
    if plan.get("stopped"):
        return unload()
    due = plan.get("next_due")
    urgent = (now >= instant(DEADLINE) or len(state.get("counted_prs", [])) >= 5
              or (state_dir / "OWNER_HOLD").exists()
              or (Path.home() / ".claude/merglbot-preauth/OWNER_HOLD").exists())
    active = bool(state.get("receipt"))
    if due and not urgent and not active and now < instant(due):
        return 0
    try:
        if invalid_state:
            raise ValueError("invalid_persisted_state")
        run_controller(state_dir)
        result_path = state_dir / "next_action.json"
        if result_path.stat().st_mtime < now.timestamp():
            raise ValueError("stale_result")
        result = json.loads(result_path.read_text())
    except (OSError, ValueError, subprocess.TimeoutExpired):
        result = emergency_cleanup(state_dir)
        atomic(state_dir / "next_action.json", result)
    plan = schedule(result, now)
    atomic(plan_path, plan)
    return unload() if plan["stopped"] else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("wake", "plist", "cleanup"))
    parser.add_argument("--state-dir", type=Path, required=True)
    args = parser.parse_args()
    state_dir = args.state_dir.resolve()
    if args.command == "cleanup":
        clean = locked_cleanup(state_dir)
        print(json.dumps({"action": "cleanup_verified" if clean else "cleanup_required",
                          "status": "inactive" if clean else "unverified",
                          "reason": "controller_runtime_gap", "history": {"unfinished_runs": 0, "data_gaps": 1}}))
        return 0
    if args.command == "plist":
        payload = {"Label": LABEL, "ProgramArguments": [sys.executable, str(Path(__file__).resolve()),
                   "wake", "--state-dir", str(state_dir)], "StartInterval": 300, "RunAtLoad": True,
                   "EnvironmentVariables": {"PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"},
                   "StandardOutPath": "/dev/null", "StandardErrorPath": "/dev/null"}
        sys.stdout.buffer.write(plistlib.dumps(payload))
        return 0
    state_dir.mkdir(parents=True, exist_ok=True)
    with (state_dir / "runtime.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 0
        return wake(state_dir, dt.datetime.now(dt.timezone.utc))


if __name__ == "__main__":
    raise SystemExit(main())
