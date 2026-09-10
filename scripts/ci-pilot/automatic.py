"""Bounded automatic experiment, using the existing lock, runtime and heartbeat."""
import base64
import datetime as dt
import json
from pathlib import Path
from urllib.parse import quote

from github_client import AUTO_MODE, AUTO_PR, DEADLINE, REPOS, Gap, digest, instant

REPO = REPOS[1]
WORKFLOW_HASH = "af63c037f1c853818320d58a552b5e9fa1ca08422dac5748763156ce670d422c"
CLASSIFIER_HASH = "8304e9c9a23bf6b828dd3b07d5f2a390c30b3a7b25b9f8d057772d44a789003a"
PATHS = {"scripts/reconcile-alert-config.py", "scripts/reconcile-alert-estate.py",
         "tests/test_reconcile_alert_config.py", "tests/test_reconcile_alert_estate.py"}


def snapshot(gh, number, protection_hash):
    from controller import eligible
    p = gh.api(f"repos/{REPO}/pulls/{number}")
    r = {"repo": REPO, "pr": number, "head": p["head"]["sha"], "base": p["base"]["sha"]}
    if gh.api(f"repos/{REPO}/git/ref/heads/main")["object"]["sha"] != r["base"]:
        raise Gap("advanced_main")
    s = gh.snapshot(r)
    content = gh.api(f"repos/{REPO}/contents/scripts/ci-delay-admission.py?ref={r['base']}")
    classifier = base64.b64decode(content["content"]).decode()
    if s["workflow_sha256"] != WORKFLOW_HASH or digest(classifier) != CLASSIFIER_HASH:
        raise Gap("unverified_experiment_source")
    if not set(s["paths"]) <= PATHS or not s["paths"] or p["labels"]:
        raise Gap("excluded_experiment_scope")
    r.update(paths=s["paths"], diff_sha256=s["diff_sha256"], protection_sha256=protection_hash,
             workflow_sha256=WORKFLOW_HASH, eligible=True, assessment="Automatic bounded path admission")
    # The experiment has a separately pinned reviewed source contract.
    eligible(r, {**s, "selector_supported": True})
    return r, p["head"]["ref"]


def observe(gh, case, now):
    """Discover intermediate heads, then reuse complete attempt-specific job measurements."""
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
    pending, gaps = 0, 0
    metrics = case.setdefault("measurements", {})
    for head in case["heads"]:
        r = {"repo": REPO, "pr": case["pr"], "head": head, "base": case["initial_base"]}
        entry = metrics.setdefault(head, {"snapshots": {}})
        signature = digest(json.dumps(sorted(signatures.get(head, []))))
        previous = entry.get("latest")
        if (previous and entry.get("inventory_signature") == signature
                and entry.get("until") == case.get("stopped_at")
                and not previous["runner_evidence_gaps"] and previous["observations"]
                and all(o["status"] == "completed" for o in previous["observations"])):
            m = previous
        else:
            m = gh.measurements(r, case["started_at"], case.get("stopped_at"))
        for observation in m["observations"]:
            key = digest(json.dumps(observation, sort_keys=True))
            entry["snapshots"].setdefault(key, {"observed_at": now.isoformat(), "evidence": observation})
            pending += observation["status"] != "completed"
        entry["latest"] = m
        entry.update(inventory_signature=signature, until=case.get("stopped_at"))
        gaps += m["runner_evidence_gaps"]
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


def experiment_tick(gh, state, now, apply=False, receipt=None, hold=False,
                    persist=lambda s: None, clock=None, hold_check=lambda: False,
                    supervisor_ready=lambda: False):
    from controller import cleanup, history_evidence
    def check_time():
        current = clock() if clock else dt.datetime.now(dt.timezone.utc)
        if max(now, current) >= instant(DEADLINE):
            raise Gap("deadline")
        if hold or hold_check() or (Path.home() / ".claude/merglbot-preauth/OWNER_HOLD").exists():
            raise Gap("owner_hold")

    experiment = state.get("experiment")
    if receipt == {"begin": True}:
        if experiment is not None:
            raise Gap("experiment_already_started")
        if not apply:
            return {"action": "begin_available", "status": "readonly"}
        check_time()
        if state.get("receipt"):
            raise Gap("old_admission_active")
        if not cleanup(gh, True):
            raise Gap("old_selectors_not_clean")
        historical = history_evidence(gh, state, now)
        if historical["unfinished_runs"] or historical["data_gaps"]:
            raise Gap("old_history_incomplete")
        experiment = {"version": 1, "started_at": now.isoformat(), "cases": [], "active": None}
        state["experiment"] = experiment
        persist(state)
        receipt = None
    if not isinstance(experiment, dict) or experiment.get("version") != 1 or not isinstance(experiment.get("cases"), list):
        raise Gap("invalid_experiment_state")
    try:
        check_time()
        if receipt == {"stop": True}:
            raise Gap("selection_complete")
        active = experiment.get("active")
        selectors = {repo: values for repo in REPOS if (values := gh.selectors(repo))}
        if receipt:
            if (receipt.get("repo") != REPO or type(receipt.get("pr")) is not int or receipt["pr"] <= 0
                    or receipt.get("kind") not in ("synthetic", "natural")
                    or receipt.get("mode") not in ("baseline", "delay") or active is not None):
                raise Gap("invalid_experiment_selection")
            existing = [c for c in experiment["cases"] if c["pr"] == receipt["pr"]]
            if any(c["kind"] != receipt["kind"] for c in existing):
                raise Gap("case_provenance_changed")
            ids = {c["pr"] for c in experiment["cases"] if c["kind"] == receipt["kind"]}
            if receipt["pr"] not in ids and len(ids) >= 3:
                raise Gap("experiment_kind_limit")
            if selectors:
                raise Gap("selectors_already_present")
            previous = histories(gh, experiment, now)
            if previous["unfinished_runs"] or previous["data_gaps"]:
                raise Gap("previous_phase_incomplete")
            r, branch = snapshot(gh, receipt["pr"], receipt["protection_sha256"])
            if not apply:
                return {"action": "activation_available", "status": "readonly"}
            if not supervisor_ready():
                raise Gap("supervisor_not_ready")
            active = {**receipt, "started_at": now.isoformat(), "branch": branch,
                      "initial_base": r["base"], "heads": [], "phase": "activating"}
            experiment["cases"].append(active)
            experiment["active"] = len(experiment["cases"]) - 1
            persist(state)
            for name, value in ((AUTO_MODE, active["mode"]), (AUTO_PR, str(active["pr"]))):
                check_time()
                snapshot(gh, active["pr"], active["protection_sha256"])
                if not supervisor_ready():
                    raise Gap("supervisor_not_ready")
                gh.mutate(REPO, name, value)
            active["phase"] = "active"
            persist(state)
        elif active is not None:
            active = experiment["cases"][active]
            if active["phase"] != "active":
                raise Gap("partial_activation")
            snapshot(gh, active["pr"], active["protection_sha256"])
        if active is not None:
            expected = {REPO: {AUTO_MODE: active["mode"], AUTO_PR: str(active["pr"])}}
            if {repo: values for repo in REPOS if (values := gh.selectors(repo))} != expected:
                raise Gap("selector_readback")
        elif selectors:
            raise Gap("orphan_selectors")
        historical = histories(gh, experiment, now)
        if historical["data_gaps"]:
            raise Gap("measurement_gap")
        check_time()
        persist(state)
        return {"action": "observe" if active is not None else "await_experiment_selection",
                "status": "active" if active is not None else "inactive", "history": historical}
    except Exception as error:
        reason = str(error) if isinstance(error, Gap) else "experiment_read_gap"
        clean = cleanup(gh, apply)
        if clean and apply and experiment.get("active") is not None:
            case = experiment["cases"][experiment["active"]]
            case.update(phase="inactive", stopped_at=now.isoformat(), reason=reason)
            experiment["active"] = None
        historical = histories(gh, experiment, now)
        persist(state)
        if clean and (historical["unfinished_runs"] or historical["data_gaps"]):
            # The existing runtime stops on cleanup_verified/deadline. Keep it alive
            # to reconcile admitted jobs after admission itself has been disabled.
            return {"action": "drain_admitted_runs", "status": "pending", "reason": reason,
                    "cleanup_verified": True, "history": historical}
        return {"action": "cleanup_verified" if clean else "cleanup_required",
                "status": "inactive" if clean else "unverified", "reason": reason, "history": historical}
