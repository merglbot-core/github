"""Read-only, attempt-specific observations for bounded experiment phases."""
import json
from urllib.parse import quote

from github_client import REPOS, Gap, digest, instant

REPO = REPOS[1]


def observe(gh, case, now):
    """Discover intermediate heads, then reuse complete attempt-specific job measurements."""
    case.pop("empty_phase_verified", None)
    query = (f"repos/{REPO}/actions/workflows/python-script-tests.yml/runs?event=pull_request"
             f"&branch={quote(case['branch'], safe='')}")
    # A rerun can enter this phase even when its original run predates it.
    # The adapter assigns each attempt by run_started_at, not original created_at.
    runs = gh.pages(query, "workflow_runs")
    if len(runs) >= 1000:
        raise Gap("run_inventory_cap")
    heads = set()
    signatures = {}
    for run in runs:
        if case.get("stopped_at") and instant(run["created_at"]) >= instant(case["stopped_at"]):
            continue
        if run["head_branch"] != case["branch"]:
            raise Gap("run_branch_mismatch")
        associations = run["pull_requests"]
        if not associations:
            associations = gh.pages(f"repos/{REPO}/commits/{run['head_sha']}/pulls")
            if len(associations) != 1:
                raise Gap("run_pr_ambiguous")
        if not any(p["number"] == case["pr"] for p in associations):
            raise Gap("run_pr_mismatch")
        heads.add(run["head_sha"])
        signatures.setdefault(run["head_sha"], []).append(
            (run["id"], run["run_attempt"], run["status"], run["updated_at"]))
    # Persist discovered heads before measuring; an API gap must not erase inventory.
    case["heads"] = sorted(set(case.get("heads", [])) | heads)
    pending, gaps, observed = 0, 0, 0
    metrics = case.setdefault("measurements", {})
    for head in case["heads"]:
        r = {"repo": REPO, "pr": case["pr"], "head": head, "base": case["initial_base"]}
        entry = metrics.setdefault(head, {"snapshots": {}})
        signature = digest(json.dumps(sorted(signatures.get(head, []))))
        previous = entry.get("latest")
        if (previous and entry.get("inventory_signature") == signature
                and entry.get("until") == case.get("stopped_at")
                and entry.get("since") == case["started_at"]
                and not previous["runner_evidence_gaps"] and previous["observations"]
                and all(o["status"] == "completed" for o in previous["observations"])):
            m = previous
        else:
            m = gh.measurements(r, case["started_at"], case.get("stopped_at"))
        observed += len(m["observations"])
        for observation in m["observations"]:
            key = digest(json.dumps(observation, sort_keys=True))
            entry["snapshots"].setdefault(key, {"observed_at": now.isoformat(), "evidence": observation})
            pending += observation["status"] != "completed"
        entry["latest"] = m
        entry.update(inventory_signature=signature, since=case["started_at"], until=case.get("stopped_at"))
        gaps += m["runner_evidence_gaps"]
    if not observed:
        # A just-selected phase awaits its first event; a closed empty phase is not proof.
        if case.get("stopped_at"):
            # Successful complete reads with no event are the expected outcome
            # of the bounded no-event timeout, not unfinished runner work.
            recoverable = {"github_command_failed", "github_command_timeout",
                           "github_command_unavailable", "invalid_api_json", "measurement_gap"}
            if case.get("reason") in recoverable and gaps == 0:
                # All inventory and per-head reads above succeeded. This proves
                # an empty interval, never a tested or successful pilot case.
                case["empty_phase_verified"] = True
            elif case.get("reason") != "compatibility_no_event_24h":
                gaps += 1
        else:
            pending += 1
    return {"unfinished_runs": pending, "data_gaps": gaps, "observed_at": now.isoformat()}


def histories(gh, experiment, now):
    result = {"unfinished_runs": 0, "data_gaps": 0, "observed_at": now.isoformat()}
    for case in experiment["cases"]:
        try:
            item = observe(gh, case, now)
            result["unfinished_runs"] += item["unfinished_runs"]
            result["data_gaps"] += item["data_gaps"]
        except Exception:
            result["data_gaps"] += 1
    return result


