#!/usr/bin/env python3
"""GitHub adapter for the bounded CI pilot; importing it performs no actions."""
import base64
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile

REPOS = ("merglbot-extractors/denatura-forecast-exporter", "merglbot-core/infra",
         "merglbot-denatura/denatura-fb-viz")
PR_VAR, SHA_VAR, BASE_VAR = "CI_DELAY_PILOT_PR", "CI_DELAY_PILOT_SHA", "CI_DELAY_PILOT_BASE_SHA"
AUTO_PR, AUTO_MODE = "CI_DEBOUNCE_TEST_PR", "CI_DEBOUNCE_TEST_MODE"
SELECTOR_NAMES = (AUTO_PR, AUTO_MODE, PR_VAR, SHA_VAR, BASE_VAR)
BASE_BOUND_REPOS = frozenset((REPOS[1],))
DEADLINE = "2026-09-14T19:03:19Z"
ENVIRONMENT = "ci-pr-delay"
WORKFLOWS = dict(zip(REPOS, (".github/workflows/ci.yml",
                            ".github/workflows/python-script-tests.yml", ".github/workflows/ci.yml")))
CHECKS = dict(zip(REPOS, (("unit-tests (3.11)", "unit-tests (3.12)"), ("Unit tests",), ("ci",))))
TRUSTED_WORKFLOW_SHA256 = dict(zip(REPOS, (
    "a69f959a475154688e97476325deed907a6733df443c4d87d4661c76e704e85c",
    "71a5a36e93245225fce8e3e7297360ebdf31895fbd36bf497f53792270767941",
    "6bc142187c566945d6fd92f7713ea09f3b4db0a5f3f386007d98ff666e5d2832")))
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
        try:
            result = subprocess.run(args, capture_output=True, text=True, timeout=90, env=env)
        except subprocess.TimeoutExpired as error:
            error.output = error.stderr = None
            raise Gap("github_command_timeout") from None
        except OSError:
            raise Gap("github_command_unavailable") from None
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
        return {r["name"]: r["value"] for r in rows if r["name"] in SELECTOR_NAMES}

    def mutate(self, repo, name, value=None):
        if (repo not in REPOS or name not in SELECTOR_NAMES
                or (name in (AUTO_PR, AUTO_MODE) and repo != REPOS[1] and value is not None)):
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
        if any(pr[k]["sha"] != receipt[k] for k in ("head", "base")):
            raise Gap("stale_receipt")
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
        if any(end[k]["sha"] != receipt[k] for k in ("head", "base")):
            raise Gap("snapshot_race")
        return {"pr": end, "paths": sorted(f["filename"] for f in files),
                "diff_sha256": digest(diff), "workflow_sha256": digest(workflow),
                "selector_supported": digest(workflow) == TRUSTED_WORKFLOW_SHA256[repo],
                "protection": protection, "rules": rules,
                "environment": env, "custom": custom, "policies": policies,
                "environment_empty": not variables and not secrets}

    def attempts(self, receipt, summary):
        if (summary["path"].split("@", 1)[0] != WORKFLOWS[receipt["repo"]]
                or summary["event"] != "pull_request"):
            return
        def bound(run):
            associations = run["pull_requests"]
            if not associations:
                if run["head_sha"] != receipt["head"]:
                    raise Gap("run_pr_binding_changed")
                associations = self.pages(f"repos/{receipt['repo']}/commits/{receipt['head']}/pulls")
                if len(associations) != 1 or associations[0]["number"] != receipt["pr"]:
                    raise Gap("commit_pr_binding_ambiguous")
                branch = run.get("head_branch")
                if branch and associations[0].get("head", {}).get("ref") != branch:
                    raise Gap("commit_pr_branch_changed")
            matches = [p for p in associations if p["number"] == receipt["pr"]]
            # Associated PR head/base are mutable; only run.head_sha is historical.
            if matches and run["head_sha"] != receipt["head"]:
                raise Gap("run_pr_binding_changed")
            return bool(matches)
        if not bound(summary):
            return
        latest = summary["run_attempt"]
        if type(latest) is not int or not 1 <= latest <= 100:
            raise Gap("attempt_enumeration_cap")
        for number in range(1, latest + 1):
            run = self.api(f"repos/{receipt['repo']}/actions/runs/{summary['id']}/attempts/{number}")
            if (not bound(run) or run["run_attempt"] != number or run["id"] != summary["id"]
                    or run["head_sha"] != receipt["head"]):
                raise Gap("attempt_binding_changed")
            yield run, latest

    def measurements(self, receipt, since, until=None):
        repo = receipt["repo"]
        runs = self.pages(f"repos/{repo}/actions/runs?head_sha={receipt['head']}", "workflow_runs")
        runner_seconds, waiting, cancelled_without_runner, count, runner_gaps = 0, 0, 0, 0, 0
        observations, run_ids = [], set()
        for run, latest in (attempt for summary in runs for attempt in self.attempts(receipt, summary)):
            # Original created_at predates natural reruns; admission is per attempt.
            if not isinstance(run.get("run_started_at"), str) or not run["run_started_at"]:
                raise Gap("attempt_start_missing")
            if instant(run["run_started_at"]) < instant(since):
                continue
            if until is not None and instant(run["run_started_at"]) >= instant(until):
                continue
            count += 1
            run_ids.add(run["id"])
            pending = []
            if run["run_attempt"] == latest:
                pending = self.api(f"repos/{repo}/actions/runs/{run['id']}/pending_deployments")
                if self.api(f"repos/{repo}/actions/runs/{run['id']}")["run_attempt"] != latest:
                    raise Gap("pending_attempt_race")
            waiting += sum(p["environment"]["name"] == ENVIRONMENT for p in pending)
            jobs = self.pages(f"repos/{repo}/actions/runs/{run['id']}/attempts/{run['run_attempt']}/jobs", "jobs")
            if any("runner_id" not in j or (j["runner_id"] is not None
                   and (type(j["runner_id"]) is not int or j["runner_id"] < 0))
                   or not isinstance(j.get("steps"), list) for j in jobs):
                raise Gap("job_runner_evidence_missing")
            observations.append({"run_id": run["id"], "attempt": run["run_attempt"], "head": run["head_sha"],
                                 "created_at": run["created_at"], "run_started_at": run["run_started_at"], "status": run["status"],
                                 "pending_environment": [p["environment"]["name"] for p in pending],
                                 "wait_timers": [{"environment": p["environment"]["name"], "wait_timer": p.get("wait_timer"),
                                                  "started_at": p.get("wait_timer_started_at")} for p in pending],
                                 "checkout_head": "DATA_GAP",
                                 "actual_base": "DATA_GAP", "receipt_base": receipt["base"],
                                 "jobs": [{**{k: j[k] for k in ("id", "name", "runner_id", "started_at", "completed_at", "conclusion")},
                                           "steps_count": len(j["steps"])}
                                          for j in jobs]})
            for job in jobs:
                # Explicit null is valid API data, but is not proof of no runner.
                runner_gaps += job["runner_id"] is None
                if job["runner_id"] == 0 and job["steps"] == [] and job["conclusion"] == "cancelled":
                    cancelled_without_runner += 1
                if job["runner_id"] and job["started_at"] and job["completed_at"]:
                    duration = (instant(job["completed_at"]) - instant(job["started_at"])).total_seconds()
                    if duration < 0:
                        runner_gaps += 1
                    else:
                        runner_seconds += duration
                elif job["runner_id"] and job["conclusion"] is not None:
                    runner_gaps += 1
        return {"runs": len(run_ids), "attempts": count, "runner_seconds": runner_seconds,
                "runner_evidence_gaps": runner_gaps,
                "pending_environment_observations": waiting,
                "cancelled_without_runner": cancelled_without_runner, "observations": observations}
