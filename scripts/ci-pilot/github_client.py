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
                                 "wait_timers": [{"environment": p["environment"]["name"], "wait_timer": p.get("wait_timer"),
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

