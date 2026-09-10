"""Bounded automatic experiment, using the existing lock, runtime and heartbeat."""
import base64
import datetime as dt
from pathlib import Path
import experiment_measurements as measurements

from github_client import AUTO_MODE, AUTO_PR, DEADLINE, REPOS, Gap, digest, instant

REPO = REPOS[1]
WORKFLOW_HASH = "cae8723a5b7dd9b87b766a174a3630f08d7bb624bc7d6cfd9bb1a186aaba7c4d"
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
    eligible(r, {**s, "selector_supported": True})
    return r, p["head"]["ref"]


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
    def history():
        cases = [c for c in experiment["cases"]
                 if not (c.get("phase") == "aborted_no_write" and c.get("write_attempted") is False)]
        return measurements.histories(gh, {**experiment, "cases": cases}, now)

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
            previous = history()
            if previous["unfinished_runs"] or previous["data_gaps"]:
                raise Gap("previous_phase_incomplete")
            r, branch = snapshot(gh, receipt["pr"], receipt["protection_sha256"])
            if not apply:
                return {"action": "activation_available", "status": "readonly"}
            if not supervisor_ready():
                raise Gap("supervisor_not_ready")
            active = {**receipt, "started_at": now.isoformat(), "branch": branch,
                      "initial_base": r["base"], "heads": [], "phase": "activating", "write_attempted": False}
            experiment["cases"].append(active)
            experiment["active"] = len(experiment["cases"]) - 1
            persist(state)
            for name, value in ((AUTO_MODE, active["mode"]), (AUTO_PR, str(active["pr"]))):
                check_time()
                snapshot(gh, active["pr"], active["protection_sha256"])
                if not supervisor_ready():
                    raise Gap("supervisor_not_ready")
                active["write_attempted"] = True
                persist(state)
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
        historical = history()
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
            stopped = max(now, clock() if clock else dt.datetime.now(dt.timezone.utc))
            phase = "aborted_no_write" if case.get("write_attempted") is False else "inactive"
            case.update(phase=phase, stopped_at=stopped.isoformat(), reason=reason)
            experiment["active"] = None
        historical = history()
        persist(state)
        if clean and (historical["unfinished_runs"] or historical["data_gaps"]):
            # Keep supervising admitted jobs after disabling admission.
            return {"action": "drain_admitted_runs", "status": "pending", "reason": reason,
                    "cleanup_verified": True, "history": historical}
        return {"action": "cleanup_verified" if clean else "cleanup_required",
                "status": "inactive" if clean else "unverified", "reason": reason, "history": historical}
