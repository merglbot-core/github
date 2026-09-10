#!/usr/bin/env python3
"""Bounded, fail-closed selector supervisor. No reviews, merges or runner install."""
import argparse
import datetime as dt
import fcntl
import json
from pathlib import Path
from github_client import CHECKS, DEADLINE, REPOS, PR_VAR, SHA_VAR, BASE_VAR, BASE_BOUND_REPOS, SELECTOR_NAMES, GitHub, Gap, atomic, digest, instant


def validate_receipt(r):
    if (r.get("repo") not in REPOS or type(r.get("pr")) is not int or r["pr"] <= 0
            or r.get("eligible") is not True or not r.get("assessment", "").strip()
            or not r.get("paths") or sorted(set(r["paths"])) != r["paths"]):
        raise Gap("invalid_semantic_receipt")
    for key, length in (("head", 40), ("base", 40), ("diff_sha256", 64),
                        ("protection_sha256", 64), ("workflow_sha256", 64)):
        value = r.get(key, "")
        if len(value) != length or any(c not in "0123456789abcdef" for c in value):
            raise Gap("invalid_receipt_digest")


def eligible(r, snap):
    validate_receipt(r)
    p = snap["pr"]
    if (p["state"] != "open" or p["draft"] or p["merged"]
            or p["base"]["ref"] != "main" or p["base"]["repo"]["full_name"] != r["repo"]
            or p["head"]["repo"]["full_name"] != r["repo"]
            or p["head"]["sha"] != r["head"] or p["base"]["sha"] != r["base"]):
        raise Gap("pr_binding_changed")
    if any("hold" in label["name"].lower() for label in p["labels"]):
        raise Gap("pr_hold")
    if snap["paths"] != r["paths"] or snap["diff_sha256"] != r["diff_sha256"]:
        raise Gap("diff_changed")
    if not snap["selector_supported"] or snap["workflow_sha256"] != r["workflow_sha256"]:
        raise Gap("main_workflow_not_verified")
    protection = {"protection": snap["protection"], "rules": snap["rules"]}
    if digest(json.dumps(protection, sort_keys=True)) != r["protection_sha256"]:
        raise Gap("protection_changed")
    checks = snap["protection"]["required_status_checks"]["checks"]
    if (not any(c["context"] == "Merglbot PR Assistant v6" for c in checks)
            or any(not any(c["context"] == name and c["app_id"] == 15368 for c in checks)
                   for name in CHECKS[r["repo"]])):
        raise Gap("required_checks_missing")
    rules = snap["environment"]["protection_rules"]
    timers = [rule for rule in rules if rule["type"] == "wait_timer"]
    if (not snap["environment_empty"] or len(timers) != 1 or timers[0]["wait_timer"] != 10
            or any(rule["type"] not in ("wait_timer", "branch_policy") for rule in rules)
            or snap["environment"]["deployment_branch_policy"] != {"protected_branches": False, "custom_branch_policies": True}
            or [(p["name"], p["type"]) for p in snap["policies"]] != [("refs/pull/*/merge", "branch")]
            or snap["custom"]["total_count"] != 0):
        raise Gap("environment_contract_changed")


def cleanup(gh, apply):
    verified = True
    for repo in REPOS:
        # A failed initial read must still attempt bounded removals.
        try:
            present = gh.selectors(repo)
        except Exception:
            present = {name: "unknown" for name in SELECTOR_NAMES}
        if apply:
            for name in SELECTOR_NAMES:
                if name in present:
                    try:
                        gh.mutate(repo, name)
                    except Exception:
                        pass  # Readback is the authority, including already absent variables.
            try:
                verified = not gh.selectors(repo) and verified
            except Exception:
                verified = False
        elif present:
            verified = False
    return verified


def record_measurements(state, receipt, metrics, now, since=None, until=None):
    key = f"{receipt['repo']}#{receipt['pr']}@{receipt['head']}"
    entry = state.setdefault("measurements", {}).setdefault(key, {})
    if since is not None:
        start = instant(since)
        intervals = dict(entry.get("interval_measurements", {}))
        previous = intervals.get(start.isoformat())
        if previous is not None and previous["until"] is not None:
            if until is None or instant(previous["until"]) != instant(until):
                raise Gap("conflicting_closed_measurement_interval")
        if until is not None and instant(until) < start:
            raise Gap("invalid_measurement_interval")
        intervals[start.isoformat()] = {"until": until, "metrics": metrics}
        ordered = sorted(intervals.items(), key=lambda item: instant(item[0]))
        for (previous_start, previous), (next_start, _) in zip(ordered, ordered[1:]):
            if previous["until"] is None or instant(previous["until"]) > instant(next_start):
                raise Gap("overlapping_measurement_intervals")
        totals = {name: 0 for name in ("runner_seconds", "runner_evidence_gaps",
                  "pending_environment_observations", "cancelled_without_runner")}
        observations = {}
        for _, interval in ordered:
            current = interval["metrics"]
            for name in totals:
                totals[name] += current.get(name, 0)
            for observation in current.get("observations", []):
                identity = (observation["run_id"], observation.get("attempt", 1))
                if identity in observations:
                    raise Gap("attempt_in_multiple_intervals")
                observations[identity] = observation
        metrics = {**totals, "observations": list(observations.values()),
                   "runs": len({identity[0] for identity in observations}), "attempts": len(observations)}
        entry["interval_measurements"] = intervals
    snapshots = entry.setdefault("snapshots", {})
    for observation in metrics.get("observations", []):
        identity = digest(json.dumps(observation, sort_keys=True))
        snapshot = snapshots.setdefault(identity, {"first_seen": now.isoformat(), "evidence": observation})
        snapshot["last_seen"] = now.isoformat()
    entry.update(metrics)
    entry["ever_observed_delay"] = entry.get("ever_observed_delay", False) or metrics["pending_environment_observations"] > 0
    counted = state.setdefault("counted_prs", [])
    case = f"{receipt['repo']}#{receipt['pr']}"
    if entry["ever_observed_delay"] and case in state.get("pr_windows", {}):
        state["pr_windows"][case]["saw_delay"] = True
    if entry["ever_observed_delay"] and case not in counted:
        counted.append(case)


def history_evidence(gh, state, now):
    pending, gaps = 0, 0
    seen = set()
    for entry in state.get("history", []):
        r = entry["receipt"]
        key = (r['repo'], r['pr'], r['head'], entry.get("started_at"), entry.get("stopped_at"))
        if key in seen:
            continue
        seen.add(key)
        try:
            stopped = entry["stopped_at"]
            if instant(stopped) < instant(entry["started_at"]):
                raise Gap("invalid_history_interval")
            metrics = gh.measurements(r, entry["started_at"], stopped)
            record_measurements(state, r, metrics, now, entry["started_at"], stopped)
            pending += sum(o["status"] != "completed" for o in metrics.get("observations", []))
            gaps += metrics.get("runner_evidence_gaps", 0)
        except Exception:
            gaps += 1
    return {"unfinished_runs": pending, "data_gaps": gaps, "observed_at": now.isoformat()}


def pr_windows(state, now):
    """Migrate retained admission intent without extending a PR's first window."""
    windows = state.get("pr_windows", {})
    if not isinstance(windows, dict):
        raise Gap("invalid_pr_windows")
    windows = {key: dict(value) if isinstance(value, dict) else value for key, value in windows.items()}
    for key, value in windows.items():
        repo, separator, number = key.rpartition("#")
        if (not separator or repo not in REPOS or not number.isdigit() or int(number) <= 0
                or not isinstance(value, dict) or set(value) != {"first_selected_at", "saw_delay"}
                or type(value["saw_delay"]) is not bool):
            raise Gap("invalid_pr_windows")
        start = instant(value["first_selected_at"])
        if start.tzinfo is None or start > now:
            raise Gap("invalid_pr_windows")
    entries = list(state.get("history", []))
    if state.get("receipt"):
        entries.append(state)
    for entry in entries:
        if entry.get("historical_only"):
            continue  # Imported run creation is not selector-admission proof.
        r = entry["receipt"]
        validate_receipt(r)
        start = entry["started_at"]
        parsed = instant(start)
        if parsed.tzinfo is None or parsed > now:
            raise Gap("invalid_pr_windows")
        key = f"{r['repo']}#{r['pr']}"
        window = windows.setdefault(key, {"first_selected_at": start, "saw_delay": False})
        if instant(start) < instant(window["first_selected_at"]):
            window["first_selected_at"] = start
        if type(entry.get("saw_delay", False)) is not bool:
            raise Gap("invalid_pr_windows")
        window["saw_delay"] |= entry.get("saw_delay", False)
    for key, window in windows.items():
        window["saw_delay"] |= key in state.get("counted_prs", [])
    state["pr_windows"] = windows


def check_pr_window(state, receipt, now):
    key = f"{receipt['repo']}#{receipt['pr']}"
    window = state.get("pr_windows", {}).get(key)
    if (window and not window["saw_delay"] and key not in state.get("counted_prs", [])
            and (now - instant(window["first_selected_at"])).total_seconds() >= 86400):
        raise Gap("no_delay_24h")


def tick(gh, state, now, apply=False, receipt=None, hold=False, persist=lambda s: None,
         clock=None, hold_check=lambda: False):
    """All exceptions lead to scoped cleanup; no partial activation is accepted."""
    r = receipt or state.get("receipt")
    def activation_check():
        current = clock() if clock else dt.datetime.now(dt.timezone.utc)
        if current >= instant(DEADLINE):
            raise Gap("deadline")
        if r is not None:
            check_pr_window(state, r, max(current, now))
        if hold_check() or (Path.home() / ".claude/merglbot-preauth/OWNER_HOLD").exists():
            raise Gap("owner_hold")
    try:
        pr_windows(state, now)
    except Exception:
        return {"action": "recovery_required", "status": "unverified",
                "cleanup_verified": cleanup(gh, apply), "reason": "invalid_pr_windows"}
    try:
        if now >= instant(DEADLINE):
            raise Gap("deadline")
        if hold:
            raise Gap("owner_hold")
        historical = history_evidence(gh, state, now)
        if historical["data_gaps"]:
            raise Gap("historical_evidence_gap")
        if len(state.get("counted_prs", [])) >= 5:
            raise Gap("case_limit")
        active = {repo: gh.selectors(repo) for repo in REPOS}
        r = receipt or state.get("receipt")
        if not r:
            if any(active.values()):
                raise Gap("orphan_selectors")
            historical = history_evidence(gh, state, now)
            persist(state)
            return {"action": "await_semantic_receipt", "status": "inactive", "history": historical}
        validate_receipt(r)
        if receipt and state.get("receipt") and receipt != state["receipt"]:
            raise Gap("active_receipt_conflict")
        eligible(r, gh.snapshot(r))
        expected = {PR_VAR: str(r["pr"]), SHA_VAR: r["head"]}
        if r["repo"] in BASE_BOUND_REPOS:
            expected[BASE_VAR] = r["base"]
        populated = {repo: values for repo, values in active.items() if values}
        installed = populated == {r["repo"]: expected}
        if populated and not installed:
            raise Gap("partial_or_multiple_selectors")
        if not state.get("receipt"):
            if populated:
                raise Gap("untracked_selectors")
            if not receipt:
                raise Gap("missing_receipt")
            check_pr_window(state, r, now)
            if not apply:
                return {"action": "activation_available", "status": "readonly"}
            key = f"{r['repo']}#{r['pr']}"
            state["pr_windows"].setdefault(key, {"first_selected_at": now.isoformat(), "saw_delay": False})
            state.update(receipt=r, started_at=now.isoformat(), saw_delay=False, phase="activating")
            persist(state)  # Durable intent precedes the first mutation.
            for name in (BASE_VAR, SHA_VAR, PR_VAR):
                if name not in expected:
                    continue
                activation_check()
                gh.mutate(r["repo"], name, expected[name])
                eligible(r, gh.snapshot(r))
            after = {repo: gh.selectors(repo) for repo in REPOS}
            if {repo: values for repo, values in after.items() if values} != {r["repo"]: expected}:
                raise Gap("activation_readback_failed")
            activation_check()
            state["phase"] = "active"
        elif not installed or state.get("phase") != "active":
            raise Gap("restart_incomplete_activation")
        metrics = gh.measurements(r, state["started_at"])
        record_measurements(state, r, metrics, now, state["started_at"])
        state["saw_delay"] = state.get("saw_delay", False) or metrics["pending_environment_observations"] > 0
        if len(state["counted_prs"]) >= 5:
            raise Gap("case_limit")
        check_pr_window(state, r, now)
        persist(state)
        historical = history_evidence(gh, state, now)
        persist(state)
        activation_check()
        if len(state["counted_prs"]) >= 5:
            raise Gap("case_limit")
        return {"action": "observe", "status": "active" if apply else "readonly",
                "history": historical,
                "metrics": {k: v for k, v in metrics.items() if k != "observations"}}
    except Exception as error:
        reason = str(error) if isinstance(error, Gap) else "missing_or_invalid_data"
        clean = cleanup(gh, apply)
        if clean and apply:
            previous = state.pop("receipt", None)
            if previous:
                state.setdefault("history", []).append({"receipt": previous, "reason": reason,
                                                         "started_at": state["started_at"],
                                                         "stopped_at": now.isoformat()})
            state["phase"] = "inactive"
        historical = history_evidence(gh, state, now)
        persist(state)
        return {"action": "cleanup_verified" if clean else "cleanup_required",
                "status": "inactive" if clean else "unverified", "reason": reason, "history": historical}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("tick", "activate", "begin-experiment", "stop-selection"))
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--receipt", type=Path)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    args.state_dir.mkdir(parents=True, exist_ok=True)
    with (args.state_dir / "controller.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 0
        state_path = args.state_dir / "state.json"
        gh = GitHub()
        try:
            state = json.loads(state_path.read_text()) if state_path.exists() else {}
            if (not isinstance(state, dict)
                    or any(not isinstance(state.get(k, default), type(default)) for k, default in
                           (("counted_prs", []), ("history", []), ("measurements", {})))
                    or any(not isinstance(case, str) for case in state.get("counted_prs", []))):
                raise Gap("invalid_durable_state")
            receipt = json.loads(args.receipt.read_text()) if args.command == "activate" and args.receipt else None
            if args.command == "activate" and receipt is None:
                raise Gap("receipt_required")
            if args.command == "stop-selection" and "experiment" not in state:
                raise Gap("experiment_required")
            extra = {}
            if args.command == "begin-experiment" or "experiment" in state:
                from automatic import experiment_tick
                from runtime import ready
                extra["supervisor_ready"] = lambda: ready(args.state_dir, dt.datetime.now(dt.timezone.utc))
                runner = experiment_tick
                if args.command == "begin-experiment":
                    receipt = {"begin": True}
                elif args.command == "stop-selection":
                    receipt = {"stop": True}
            else:
                runner = tick
            result = runner(gh, state, dt.datetime.now(dt.timezone.utc), args.apply, receipt,
                          ((args.state_dir / "OWNER_HOLD").exists()
                           or (Path.home() / ".claude/merglbot-preauth/OWNER_HOLD").exists()),
                          lambda s: atomic(state_path, s),
                          hold_check=lambda: (args.state_dir / "OWNER_HOLD").exists(), **extra)
        except Exception:
            clean = cleanup(gh, args.apply)
            result = {"action": "recovery_required", "cleanup_verified": clean,
                      "status": "unverified", "reason": "invalid_local_state"}
        atomic(args.state_dir / "next_action.json", result)
        print(json.dumps(result, sort_keys=True))
        return 1 if result["status"] == "unverified" else 0


if __name__ == "__main__":
    raise SystemExit(main())
