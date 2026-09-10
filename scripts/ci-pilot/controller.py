#!/usr/bin/env python3
"""Bounded, fail-closed selector supervisor. No reviews, merges or runner install."""
import argparse
import base64
import datetime as dt
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile

REPOS = ("merglbot-extractors/denatura-forecast-exporter", "merglbot-core/infra",
         "merglbot-denatura/denatura-fb-viz")
PR_VAR, SHA_VAR = "CI_DELAY_PILOT_PR", "CI_DELAY_PILOT_SHA"
DEADLINE = "2026-09-14T19:03:19Z"
ENVIRONMENT = "ci-pr-delay"
WORKFLOWS = dict(zip(REPOS, (".github/workflows/ci.yml",
                            ".github/workflows/python-script-tests.yml", ".github/workflows/ci.yml")))
CHECKS = dict(zip(REPOS, (("unit-tests (3.11)", "unit-tests (3.12)"), ("Unit tests",), ("ci",))))
PREDICATE = ("github.event_name == 'pull_request' && github.event.pull_request.head.repo.full_name == github.repository && "
             "github.event.pull_request.base.ref == 'main' && format('{0}', github.event.pull_request.number) == vars.CI_DELAY_PILOT_PR && "
             "github.event.pull_request.head.sha == vars.CI_DELAY_PILOT_SHA")
GUARD = Path.home() / ".codex/bin/codex-guarded-command"


class Gap(Exception):
    pass


def instant(value):
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def atomic(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=".pilot-")
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


class GitHub:
    def command(self, args, raw=False, env=None):
        result = subprocess.run(args, capture_output=True, text=True, timeout=90, env=env)
        if result.returncode:
            raise Gap("github_command_failed")  # Never persist stderr or payloads.
        try:
            return result.stdout if raw else json.loads(result.stdout or "null")
        except ValueError:
            raise Gap("invalid_api_json") from None

    def api(self, path):
        return self.command(["gh", "api", path])

    def pages(self, path, key=None, size=100):
        output = []
        for page in range(1, 101):
            data = self.api(f"{path}{'&' if '?' in path else '?'}per_page={size}&page={page}")
            rows = data[key] if key else data
            if not isinstance(rows, list):
                raise Gap("invalid_page")
            output.extend(rows)
            if len(rows) < size:
                if key and "total_count" in data and len(output) != data["total_count"]:
                    raise Gap("incomplete_pagination")
                return output
        raise Gap("pagination_cap")

    def selectors(self, repo):
        rows = self.pages(f"repos/{repo}/actions/variables", "variables", size=30)
        return {r["name"]: r["value"] for r in rows if r["name"] in (PR_VAR, SHA_VAR)}

    def mutate(self, repo, name, value=None):
        if repo not in REPOS or name not in (PR_VAR, SHA_VAR):
            raise Gap("mutation_out_of_scope")
        args = ["/bin/bash", str(GUARD), "gh", "variable",
                "delete" if value is None else "set", name, "--repo", repo]
        if value is not None:
            args += ["--body", str(value)]
        self.command(args, raw=True, env={**os.environ, "CODEX_GUARD_ALLOW_GITHUB_MUTATION": "1"})

    def snapshot(self, receipt):
        repo, number = receipt["repo"], receipt["pr"]
        prefix = f"repos/{repo}"
        pr = self.api(f"{prefix}/pulls/{number}")
        files = self.pages(f"{prefix}/pulls/{number}/files")
        if len(files) != pr["changed_files"] or len(files) >= 3000:
            raise Gap("incomplete_files")
        diff = self.command(["gh", "api", f"{prefix}/pulls/{number}", "-H",
                             "Accept: application/vnd.github.diff"], raw=True)
        protection = self.api(f"{prefix}/branches/main/protection")
        content = self.api(f"{prefix}/contents/{WORKFLOWS[repo]}?ref={receipt['base']}")
        workflow = base64.b64decode(content["content"], validate=False).decode()
        rules = self.pages(f"{prefix}/rules/branches/main")
        env = self.api(f"{prefix}/environments/{ENVIRONMENT}")
        custom = self.api(f"{prefix}/environments/{ENVIRONMENT}/deployment_protection_rules")
        policies = self.pages(f"{prefix}/environments/{ENVIRONMENT}/deployment-branch-policies", "branch_policies")
        variables = self.pages(f"{prefix}/environments/{ENVIRONMENT}/variables", "variables", 30)
        secrets = self.pages(f"{prefix}/environments/{ENVIRONMENT}/secrets", "secrets", 100)
        # Re-read the PR after all dependent reads to detect moving heads/bases.
        end = self.api(f"{prefix}/pulls/{number}")
        if any(pr[k]["sha"] != end[k]["sha"] for k in ("head", "base")):
            raise Gap("snapshot_race")
        return {"pr": end, "paths": sorted(f["filename"] for f in files),
                "diff_sha256": digest(diff), "workflow_sha256": digest(workflow),
                "selector_supported": PREDICATE in workflow,
                "protection": protection, "rules": rules,
                "environment": env, "custom": custom, "policies": policies,
                "environment_empty": not variables and not secrets}

    def measurements(self, receipt, since):
        repo = receipt["repo"]
        runs = self.pages(f"repos/{repo}/actions/runs?head_sha={receipt['head']}", "workflow_runs")
        runner_seconds, waiting, cancelled_without_runner, count = 0, 0, 0, 0
        observations = []
        for run in runs:
            if (instant(run["created_at"]) < instant(since)
                    or run["path"] != WORKFLOWS[repo] or run["event"] != "pull_request"):
                continue
            bindings = run["pull_requests"]
            if not bindings:
                raise Gap("run_pr_binding_missing")
            matches = [p for p in bindings if p["number"] == receipt["pr"]]
            if not matches:
                continue
            if any(p["head"]["sha"] != receipt["head"] or p["base"]["sha"] != receipt["base"] for p in matches):
                raise Gap("run_pr_binding_changed")
            count += 1
            pending = self.api(f"repos/{repo}/actions/runs/{run['id']}/pending_deployments")
            waiting += sum(p["environment"]["name"] == ENVIRONMENT for p in pending)
            jobs = self.pages(f"repos/{repo}/actions/runs/{run['id']}/jobs?filter=all", "jobs")
            observations.append({"run_id": run["id"], "attempt": run["run_attempt"], "head": run["head_sha"],
                                 "created_at": run["created_at"], "status": run["status"],
                                 "pending_environment": [p["environment"]["name"] for p in pending],
                                 "wait_timers": [{"environment": p["environment"]["name"],
                                                  "wait_timer": p.get("wait_timer"),
                                                  "started_at": p.get("wait_timer_started_at")} for p in pending],
                                 "checkout_head": "DATA_GAP",
                                 "jobs": [{**{k: j[k] for k in ("id", "name", "runner_id", "started_at", "completed_at", "conclusion")},
                                           "steps_count": len(j["steps"])}
                                          for j in jobs]})
            for job in jobs:
                if job["runner_id"] == 0 and job["steps"] == [] and job["conclusion"] == "cancelled":
                    cancelled_without_runner += 1
                if job["runner_id"] and job["started_at"] and job["completed_at"]:
                    runner_seconds += max(0, (instant(job["completed_at"]) - instant(job["started_at"])).total_seconds())
        return {"runs": count, "runner_seconds": runner_seconds,
                "pending_environment_observations": waiting,
                "cancelled_without_runner": cancelled_without_runner, "observations": observations}


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
            present = {PR_VAR: "unknown", SHA_VAR: "unknown"}
        if apply:
            for name in (PR_VAR, SHA_VAR):
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


def record_measurements(state, receipt, metrics, now):
    key = f"{receipt['repo']}#{receipt['pr']}@{receipt['head']}"
    entry = state.setdefault("measurements", {}).setdefault(key, {})
    snapshots = entry.setdefault("snapshots", {})
    for observation in metrics.get("observations", []):
        identity = digest(json.dumps(observation, sort_keys=True))
        snapshot = snapshots.setdefault(identity, {"first_seen": now.isoformat(), "evidence": observation})
        snapshot["last_seen"] = now.isoformat()
    entry.update(metrics)
    entry["ever_observed_delay"] = entry.get("ever_observed_delay", False) or metrics["pending_environment_observations"] > 0
    counted = state.setdefault("counted_prs", [])
    case = f"{receipt['repo']}#{receipt['pr']}"
    if entry["ever_observed_delay"] and case not in counted:
        counted.append(case)


def history_evidence(gh, state, now):
    pending, gaps = 0, 0
    seen = set()
    for entry in state.get("history", []):
        r = entry["receipt"]
        key = f"{r['repo']}#{r['pr']}@{r['head']}"
        if key in seen:
            continue
        seen.add(key)
        try:
            metrics = gh.measurements(r, entry["started_at"])
            record_measurements(state, r, metrics, now)
            pending += sum(o["status"] != "completed" for o in metrics.get("observations", []))
        except Exception:
            gaps += 1
    return {"unfinished_runs": pending, "data_gaps": gaps, "observed_at": now.isoformat()}


def tick(gh, state, now, apply=False, receipt=None, hold=False, persist=lambda s: None,
         clock=None, hold_check=lambda: False):
    """All exceptions lead to scoped cleanup; no partial activation is accepted."""
    def activation_check():
        if (clock() if clock else dt.datetime.now(dt.timezone.utc)) >= instant(DEADLINE):
            raise Gap("deadline")
        if hold_check() or (Path.home() / ".claude/merglbot-preauth/OWNER_HOLD").exists():
            raise Gap("owner_hold")
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
        populated = {repo: values for repo, values in active.items() if values}
        installed = populated == {r["repo"]: expected}
        if populated and not installed:
            raise Gap("partial_or_multiple_selectors")
        if not state.get("receipt"):
            if populated:
                raise Gap("untracked_selectors")
            if not receipt:
                raise Gap("missing_receipt")
            if not apply:
                return {"action": "activation_available", "status": "readonly"}
            state.update(receipt=r, started_at=now.isoformat(), saw_delay=False, phase="activating")
            persist(state)  # Durable intent precedes the first mutation.
            activation_check()
            gh.mutate(r["repo"], SHA_VAR, r["head"])
            eligible(r, gh.snapshot(r))
            activation_check()
            gh.mutate(r["repo"], PR_VAR, str(r["pr"]))
            eligible(r, gh.snapshot(r))
            after = {repo: gh.selectors(repo) for repo in REPOS}
            if {repo: values for repo, values in after.items() if values} != {r["repo"]: expected}:
                raise Gap("activation_readback_failed")
            activation_check()
            state["phase"] = "active"
        elif not installed or state.get("phase") != "active":
            raise Gap("restart_incomplete_activation")
        metrics = gh.measurements(r, state["started_at"])
        record_measurements(state, r, metrics, now)
        state["saw_delay"] = state.get("saw_delay", False) or metrics["pending_environment_observations"] > 0
        if len(state["counted_prs"]) >= 5:
            raise Gap("case_limit")
        if not state["saw_delay"] and (now - instant(state["started_at"])).total_seconds() >= 86400:
            raise Gap("no_delay_24h")
        persist(state)
        historical = history_evidence(gh, state, now)
        persist(state)
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
    parser.add_argument("command", choices=("tick", "activate"))
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
            receipt = json.loads(args.receipt.read_text()) if args.command == "activate" and args.receipt else None
            if args.command == "activate" and receipt is None:
                raise Gap("receipt_required")
            result = tick(gh, state, dt.datetime.now(dt.timezone.utc), args.apply, receipt,
                          ((args.state_dir / "OWNER_HOLD").exists()
                           or (Path.home() / ".claude/merglbot-preauth/OWNER_HOLD").exists()),
                          lambda s: atomic(state_path, s),
                          hold_check=lambda: (args.state_dir / "OWNER_HOLD").exists())
        except Exception:
            clean = cleanup(gh, args.apply)
            result = {"action": "cleanup_verified" if clean else "cleanup_required",
                      "status": "inactive" if clean else "unverified", "reason": "invalid_local_state"}
        atomic(args.state_dir / "next_action.json", result)
        print(json.dumps(result, sort_keys=True))
        return 1 if result["status"] == "unverified" else 0


if __name__ == "__main__":
    raise SystemExit(main())
