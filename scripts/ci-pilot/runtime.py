#!/usr/bin/env python3
"""One launchd wakeup; no model, daemon loop or runtime installation."""
import argparse
import datetime as dt
import fcntl
import json
import os
from pathlib import Path
import plistlib
import re
import signal
import subprocess
import sys
import heartbeat

from controller import DEADLINE, GitHub, atomic, cleanup, instant

LABEL = "ai.merglbot.ci-pilot-supervisor"


def ready(state_dir, now):
    """Require recent successful supervision by this exact loaded release."""
    try:
        plan = json.loads((state_dir / "runtime.json").read_text())
        age = (now - instant(plan["last_successful_wake"])).total_seconds()
        if plan.get("stopped") is not False or plan.get("healthy") is not True or not 0 <= age <= 360:
            return False
        result = subprocess.run(["/bin/launchctl", "print", f"gui/{os.getuid()}/{LABEL}"],
                                capture_output=True, text=True, timeout=20)
        block = re.search(r"arguments = \{\n(.*?)\n\s*\}", result.stdout, re.S)
        expected = [sys.executable, str(Path(__file__).resolve()), "wake", "--state-dir", str(state_dir.resolve())]
        return (result.returncode == 0 and block is not None
                and re.search(r"(?m)^\s*run interval = 300 seconds\s*$", result.stdout) is not None
                and [line.strip() for line in block[1].splitlines()] == expected)
    except (OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired):
        return False


def recover_experiment(state_dir, now):
    """Rearm only an inactive bounded experiment; preserve all historical state."""
    import controller
    from controller import history_evidence
    from github_client import Gap
    with (state_dir / "controller.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if getattr(controller, "EXPERIMENT_STATE_VERSION", None) != 1:
            raise Gap("experiment_controller_not_installed")
        state = json.loads((state_dir / "state.json").read_text())
        experiment = state.get("experiment")
        if (now >= instant(DEADLINE) or (state_dir / "OWNER_HOLD").exists()
                or (Path.home() / ".claude/merglbot-preauth/OWNER_HOLD").exists()
                or state.get("receipt") or not isinstance(experiment, dict)
                or experiment.get("version") != 1 or experiment.get("active") is not None
                or experiment.get("cases") != []):
            raise Gap("experiment_recovery_ineligible")
        gh = GitHub()
        if not cleanup(gh, True):
            raise Gap("experiment_recovery_cleanup_gap")
        history = history_evidence(gh, state, now)
        if history["unfinished_runs"] or history["data_gaps"]:
            raise Gap("experiment_recovery_history_gap")
        # No health assertion: a real successful wake and loaded job are still required.
        atomic(state_dir / "runtime.json", {"stopped": False, "next_due": now.isoformat(),
                                           "admitted_runs": "observed_complete"})
    return 0


def schedule(result, now):
    terminal = (result.get("action") == "cleanup_verified"
                and result.get("reason") in ("deadline", "case_limit"))
    history = result.get("history") or {}
    active = result.get("status") in ("active", "unverified", "pending") or history.get("unfinished_runs", 0) > 0
    return {"stopped": terminal,
            "next_due": None if terminal else (now + dt.timedelta(seconds=300 if active else 900)).isoformat(),
            "admitted_runs": "DATA_GAP" if (result.get("status") in ("unverified", "pending")
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


def sync_heartbeat(state_dir, plan, now):
    active = bool(plan.get("next_due")) and instant(plan["next_due"]) <= now + dt.timedelta(minutes=5)
    proof = heartbeat.sync(state_dir, active, plan["stopped"], now)
    plan["heartbeat"] = proof
    if proof["status"] == "unverified" and not active:
        # Genuine errors target the conservative cadence without bypassing validation.
        plan["heartbeat"]["retry"] = heartbeat.sync(state_dir, True, plan["stopped"], now)
    if proof["status"] in ("unverified", "pending"):
        plan.update(stopped=False, next_due=(now + dt.timedelta(minutes=5)).isoformat(), admitted_runs="DATA_GAP")
    return proof["status"] not in ("unverified", "pending")


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
        verified = sync_heartbeat(state_dir, plan, now)
        atomic(plan_path, plan)
        return unload() if verified else 1
    due = plan.get("next_due")
    urgent = (now >= instant(DEADLINE) or len(state.get("counted_prs", [])) >= 5
              or (state_dir / "OWNER_HOLD").exists()
              or (Path.home() / ".claude/merglbot-preauth/OWNER_HOLD").exists())
    active = bool(state.get("receipt")) or (isinstance(state.get("experiment"), dict)
              and state["experiment"].get("active") is not None)
    if due and not urgent and not active and now < instant(due):
        if plan.get("healthy") is True:
            plan["last_successful_wake"] = now.isoformat()
            atomic(plan_path, plan)
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
        if invalid_state:
            result = {**result, "cleanup_verified": result.get("action") == "cleanup_verified",
                      "action": "recovery_required", "status": "unverified",
                      "reason": "invalid_persisted_state"}
        atomic(state_dir / "next_action.json", result)
    plan = schedule(result, now)
    verified = sync_heartbeat(state_dir, plan, now)
    if not verified:
        result.update(action="heartbeat_sync_required", status=plan["heartbeat"]["status"])
        atomic(state_dir / "next_action.json", result)
    plan["healthy"] = verified and result.get("status") in ("active", "inactive")
    if plan["healthy"]:
        plan["last_successful_wake"] = now.isoformat()
    atomic(plan_path, plan)
    return unload() if plan["stopped"] else 0 if verified else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("wake", "plist", "cleanup", "recover-experiment"))
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
        now = dt.datetime.now(dt.timezone.utc)
        if args.command == "recover-experiment":
            return recover_experiment(state_dir, now)
        return wake(state_dir, now)


if __name__ == "__main__":
    raise SystemExit(main())
