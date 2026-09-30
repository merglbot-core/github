"""Shared helpers for the #888/#910 autopilots (owner plan of 30 Sep 2026).

Installed next to both autopilots (like cost_semantic.py). Every function receives its
GitHub accessors as arguments so tests can stub them, performs only reads, and fails
closed: an unreadable input returns "not verified" and the autopilot simply retries on
its next sweep. Python 3.9 compatible (the launchd interpreter): no PEP 604 unions, no
match statements, no 3.11 UTC constant, and timestamps go through parse_utc().
"""
import base64
import datetime as dt
import json
import math
import re

HUB = "merglbot-core/github"
# Values accepted for the pinned hub's required inputs; any other required input is
# unresolved and fails closed (the hub rejects e.g. pull-request-number 0).
REQUIRED_INPUT_FORMS = {
    "pull-request-number": re.compile(r"^\$\{\{\s*github\.event\.pull_request\.number\s*\}\}$"),
}
HUB_PR_GATE = ".github/workflows/pr-gate.yml"
SHA = re.compile(r"^[0-9a-f]{40}$")
DATA_GAP_AFTER = dt.timedelta(hours=72)
RETRY_AFTER = dt.timedelta(hours=1)

VERDICT_NOTES = {
    "FAKT": "Úspora dosáhla aspoň 80 % modelu, sub-issue zavírám.",
    "PARTIAL": ("Úspora je kladná, ale pod 80 % modelu. Podle rozhodnutí ownera z 30. 9. 2026 "
                "ji zapisuji a sub-issue zavírám jako částečně splněné."),
    "BEZ ÚSPORY": "Úspora vyšla nulová nebo záporná. Sub-issue nechávám otevřené, rozhodne owner.",
    "DATA_GAP": "Částky nejsou konečná čísla, verdikt nevydávám. Sub-issue nechávám otevřené.",
}


def parse_utc(value):
    """ISO-8601 to an aware UTC datetime; accepts the trailing Z that 3.9 fromisoformat rejects."""
    if not isinstance(value, str) or not value:
        raise ValueError("timestamp missing")
    moment = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError("timestamp lacks timezone")
    return moment.astimezone(dt.timezone.utc)


def _finite(value):
    """A real int/float (never bool) that converts to a finite float, else None."""
    if type(value) not in (int, float):
        return None
    try:
        number = float(value)
    except OverflowError:
        return None
    return number if math.isfinite(number) else None


def billing_verdict(saved_usd, model_usd):
    """Owner decision 30 Sep 2026: FAKT closes, PARTIAL closes, BEZ ÚSPORY stays open.
    A non-positive saving never closes, whatever the model; non-finite input is a data gap (V6 #969)."""
    saved_usd, model_usd = _finite(saved_usd), _finite(model_usd)
    if saved_usd is None or model_usd is None or model_usd <= 0:
        return "DATA_GAP"  # booleans, overflow, NaN/inf or a non-positive model (V6 #969)
    if saved_usd <= 0:
        return "BEZ ÚSPORY"
    if saved_usd >= 0.8 * model_usd:
        return "FAKT"
    return "PARTIAL"


def closes_billing(verdict):
    return verdict in ("FAKT", "PARTIAL")


def verdict_note(verdict):
    return VERDICT_NOTES.get(verdict, "")


def billing_retry_blocked(billing, now):
    """Missing billing data is retried hourly instead of every tick; a malformed stamp
    never blocks the retry."""
    try:
        return bool(billing.get("retry_after")) and now < parse_utc(billing["retry_after"])
    except (AttributeError, TypeError, ValueError):
        return False


def billing_data_gap_due(due_at, now):
    """72 hours after the due time without usable data, one DATA_GAP comment is written;
    a malformed due time never triggers it."""
    try:
        return bool(due_at) and now - parse_utc(due_at) >= DATA_GAP_AFTER
    except (TypeError, ValueError):
        return False


def data_gap_body(sub):
    return ("### Akceptace zatím bez dat\n\nBilling usage API ani 72 hodin po termínu nevrací "
            "úplná data pro všechny organizace v rozsahu. Verdikt proto nevydávám (DATA_GAP) a "
            f"zkouším dál jednou za hodinu. #{sub} zůstává otevřené.")


def _main_sha(gh_json, repo):
    branch = gh_json(f"repos/{repo}/branches/main")
    sha = ((branch or {}).get("commit") or {}).get("sha") or ""
    return sha if SHA.match(sha) else None


def _text(gh_json, repo, path, ref):
    data = gh_json(f"repos/{repo}/contents/{path}?ref={ref}")
    if not isinstance(data, dict) or data.get("encoding") != "base64" or not data.get("content"):
        return None
    return base64.b64decode(data["content"]).decode("utf-8", "replace")


def parse_workflow(text):
    """Structured parse (PyYAML ships with the launchd Python). Text inside block scalars or
    comments can therefore never pose as a job (V6 #969). Returns (doc, reason)."""
    try:
        import yaml
    except ImportError:
        return None, "PyYAML unavailable"
    try:
        doc = yaml.safe_load(text)
    except yaml.YAMLError:
        return None, "workflow YAML unparseable"
    if not isinstance(doc, dict) or not isinstance(doc.get("jobs"), dict):
        return None, "workflow has no jobs mapping"
    return doc, ""


def hub_jobs(doc, hub_path=HUB_PR_GATE):
    """Every job whose own `uses:` calls the hub workflow, at any ref: [(job_id, ref, job)].
    Callers count all of them, so an extra unpinned call cannot hide; owner and repository
    compare case-insensitively like GitHub does, the workflow path exactly (V6 #969)."""
    found = []
    for job_id, job in doc["jobs"].items():
        uses = job.get("uses") if isinstance(job, dict) else None
        parts = uses.strip().split("/", 2) if isinstance(uses, str) else []
        if len(parts) != 3 or "/".join(parts[:2]).lower() != HUB or "@" not in parts[2]:
            continue
        path, ref = parts[2].split("@", 1)
        if path == hub_path:
            found.append((job_id, ref, job))
    return found


def runs_on_pull_requests(doc, job):
    """The caller must run on pull requests to main and the job must not be disabled (V6 #969)."""
    triggers = doc.get("on", doc.get(True))
    if isinstance(triggers, str):
        triggers = {triggers: None}
    elif isinstance(triggers, list):
        triggers = {t: None for t in triggers}
    if not isinstance(triggers, dict) or "pull_request" not in triggers:
        return False
    spec = triggers.get("pull_request") or {}
    if not isinstance(spec, dict):
        return False
    # Any filter that could keep an ordinary PR to main from running is unresolved here
    # and fails closed; only an explicit `branches` list naming main (or `*`/`**`) and
    # activity types covering opened/synchronize/reopened are understood (V6 #969).
    if set(spec) - {"branches", "types"}:
        return False
    branches = spec.get("branches")
    if branches is not None and not (isinstance(branches, list) and all(isinstance(b, str) for b in branches)
                                     and not any(b.startswith("!") for b in branches)
                                     and any(b in ("main", "*", "**") for b in branches)):
        return False  # negated or unresolved patterns fail closed (V6 #969)
    types = spec.get("types")
    if types is not None and not (isinstance(types, list) and {"opened", "synchronize", "reopened"} <= set(types)):
        return False
    # A job-level condition or a dependency on other jobs is unresolved here and could skip
    # the gate (V6 #969).
    return "if" not in job and not job.get("needs")


def hub_runner(hub_text, value):
    """Runner the pinned hub workflow really uses for input `runs-on` = value. Only a literal
    label, a pass-through `${{ inputs.runs-on }}` or the allowlist form
    `${{ inputs.runs-on == 'X' && 'X' || 'Y' }}` are understood; anything else is None."""
    try:
        import yaml
        doc = yaml.safe_load(hub_text)
    except Exception:  # noqa: BLE001 - missing PyYAML or bad YAML both mean "unknown"
        return None
    jobs = doc.get("jobs") if isinstance(doc, dict) else None
    if not isinstance(jobs, dict) or not jobs:
        return None
    runners = set()
    for job in jobs.values():
        spec = job.get("runs-on") if isinstance(job, dict) else None
        if not isinstance(spec, str):
            return None
        spec = spec.strip()
        allow = re.fullmatch(r"\$\{\{\s*inputs\.runs-on\s*==\s*'([\w.-]+)'\s*&&\s*'([\w.-]+)'\s*\|\|\s*'([\w.-]+)'\s*\}\}", spec)
        if "${{" not in spec:
            runners.add(spec)
        elif re.fullmatch(r"\$\{\{\s*inputs\.runs-on\s*\}\}", spec):
            runners.add(value)
        elif allow and allow.group(1) == allow.group(2):
            runners.add(allow.group(2) if value == allow.group(1) else allow.group(3))
        else:
            return None
    return runners.pop() if len(runners) == 1 else None


def hub_input_default(text, name):
    """Default of a workflow_call input of the hub workflow; None when absent or an expression."""
    try:
        import yaml
        doc = yaml.safe_load(text)
    except Exception:  # noqa: BLE001 - missing PyYAML or bad YAML both mean "unknown"
        return None
    if not isinstance(doc, dict):
        return None
    triggers = doc.get("on", doc.get(True))  # YAML 1.1 reads a bare `on` key as True
    call = (triggers or {}).get("workflow_call") if isinstance(triggers, dict) else None
    spec = (((call or {}).get("inputs") or {}).get(name)) if isinstance(call, dict) else None
    default = spec.get("default") if isinstance(spec, dict) else None
    return default if isinstance(default, str) and "${{" not in default else None


def live_caller_config(gh_json, repo, workflow, pr_record=None, label=None):
    """Return-based contract: any unexpected input shape reports "not verified" (V6 #969)."""
    try:
        return _live_caller_config(gh_json, repo, workflow, pr_record, label)
    except Exception as error:  # noqa: BLE001 - never raise into the autopilot sweep
        return {"ok": False, "reason": f"verification error: {type(error).__name__}"}


def _live_caller_config(gh_json, repo, workflow, pr_record=None, label=None):
    """Owner rule 8 (30 Sep 2026): verify a low-traffic row's caller live on main.

    Checks the workflow directory at the current main SHA (files the rollout PR added are
    present, files it deleted are absent), exactly one pinned hub PR Gate call, and when a
    label is given the effective runner (explicit `with: runs-on` or the hub input default
    at the pinned SHA). Re-reads main at the end so a moving branch never passes."""
    main = _main_sha(gh_json, repo)
    if not main:
        return {"ok": False, "reason": "main unreadable"}
    listing = gh_json(f"repos/{repo}/contents/.github/workflows?ref={main}")
    if not isinstance(listing, list):
        return {"ok": False, "reason": "workflow listing unreadable", "main_sha": main}
    names = {entry.get("path") for entry in listing if isinstance(entry, dict)}
    # The rollout record is evidence: both lists must exist (possibly empty) (V6 #969).
    record = pr_record if isinstance(pr_record, dict) else None
    lists = [record.get(k) if record else None for k in ("expected_files", "deleted_files")]
    if not all(isinstance(v, list) and all(isinstance(x, str) for x in v) for v in lists):
        return {"ok": False, "reason": "rollout record unavailable or incomplete", "main_sha": main}
    expected = [p for p in lists[0] if p.startswith(".github/workflows/")]
    deleted = [p for p in lists[1] if p.startswith(".github/workflows/")]
    path = ".github/workflows/" + workflow
    missing = sorted(p for p in set(expected) | {path} if p not in names)
    present = sorted(p for p in deleted if p in names)
    if missing or present:
        return {"ok": False, "reason": f"workflow files differ: missing {missing}, still present {present}",
                "main_sha": main}
    text = _text(gh_json, repo, path, main)
    if text is None:
        return {"ok": False, "reason": "caller unreadable", "main_sha": main}
    doc, reason = parse_workflow(text)
    if doc is None:
        return {"ok": False, "reason": reason, "main_sha": main}
    calls = hub_jobs(doc)
    if len(calls) != 1 or not SHA.match(calls[0][1]):
        return {"ok": False, "reason": f"expected exactly one hub PR Gate call pinned to a full SHA, found "
                f"{[ref for _, ref, _ in calls]}", "main_sha": main}
    job_id, hub_sha, job = calls[0]
    if not runs_on_pull_requests(doc, job):
        return {"ok": False, "reason": "caller does not provably run on pull requests to main", "main_sha": main}
    # The pinned hub revision must exist and be a reusable workflow, runner check or not.
    hub = _text(gh_json, HUB, HUB_PR_GATE, hub_sha)
    hub_doc = parse_workflow(hub)[0] if hub else None
    triggers = (hub_doc or {}).get("on", (hub_doc or {}).get(True)) if hub_doc else None
    if not isinstance(triggers, dict) or "workflow_call" not in triggers:
        return {"ok": False, "reason": "pinned hub workflow missing or not reusable", "main_sha": main}
    # Every input the pinned hub requires must be passed, or the call is invalid (V6 #969).
    call = triggers.get("workflow_call") or {}
    declared = (call.get("inputs") or {}) if isinstance(call, dict) else {}
    passed = job.get("with") if isinstance(job.get("with"), dict) else {}
    required = sorted(name for name, spec in declared.items() if isinstance(spec, dict) and spec.get("required") is True)
    invalid = [name for name in required if name not in REQUIRED_INPUT_FORMS or not isinstance(passed.get(name), str)
               or not REQUIRED_INPUT_FORMS[name].match(passed[name].strip())]
    if invalid:
        return {"ok": False, "reason": f"required hub inputs missing or unsupported: {invalid}", "main_sha": main}
    undeclared = sorted(name for name in passed if name not in declared)
    if undeclared:  # GitHub rejects a call with inputs the workflow does not declare (V6 #969)
        return {"ok": False, "reason": f"caller passes inputs the pinned hub does not declare: {undeclared}",
                "main_sha": main}
    evidence = {"ok": True, "reason": "caller verified on main", "main_sha": main,
                "hub_sha": hub_sha, "job": job_id, "workflow": path}
    if label:
        # Only the hub-calling job's own `with.runs-on` counts; an expression is unresolved
        # and fails closed; without the input the pinned hub default applies. Either way the
        # pinned hub workflow decides the effective runner (V6 #969).
        inputs = job.get("with") if isinstance(job.get("with"), dict) else {}
        if "runs-on" in inputs:
            value, source = inputs["runs-on"], "with.runs-on"
            if not isinstance(value, str) or "${{" in value:
                return {"ok": False, "reason": f"runs-on {value!r} is not a literal label", "main_sha": main}
        else:
            value, source = hub_input_default(hub, "runs-on"), "hub default"
        runner = hub_runner(hub, value)
        if runner != label:
            return {"ok": False, "reason": f"effective runner {runner!r} ({source}) is not {label}",
                    "main_sha": main}
        evidence.update(runner=runner, runner_source=source)
    if _main_sha(gh_json, repo) != main:
        return {"ok": False, "reason": "main moved during verification", "main_sha": main}
    return evidence


def owner_exception_live_ok(gh_json, item):
    """Return-based contract: unexpected shapes are (False, reason), never an exception."""
    try:
        return _owner_exception_live_ok(gh_json, item)
    except Exception as error:  # noqa: BLE001
        return False, f"verification error: {type(error).__name__}"


def _owner_exception_live_ok(gh_json, item):
    """#895 rows: the rollout PR stayed closed unmerged and the recorded gitleaks contexts are
    still required on main. Returns (ok, reason)."""
    exception = item.get("owner_exception") or {}
    key, contexts = exception.get("pr"), exception.get("required_contexts") or []
    if not isinstance(key, str) or "#" not in key or not contexts or not isinstance(contexts, list) \
            or not all(isinstance(c, str) and c for c in contexts):
        return False, "exception lacks its PR or required contexts"
    repo, number = key.rsplit("#", 1)
    pr = gh_json(f"repos/{repo}/pulls/{number}")
    if not isinstance(pr, dict) or pr.get("state") != "closed" or pr.get("merged") is not False:
        return False, f"{key} is not closed unmerged"
    checks = gh_json(f"repos/{repo}/branches/main/protection/required_status_checks")
    required = {check.get("context") for check in (checks or {}).get("checks") or [] if isinstance(check, dict)}
    if not set(contexts) <= required:
        return False, f"required checks on main lost {sorted(set(contexts) - required)}"
    return True, f"{key} zavřen bez merge, povinné {', '.join(contexts)}"


# A live caller check older than this no longer verifies a low-traffic row; the sweep measures
# the row again and the closeout waits for a fresh check (V6 #970).
LOW_TRAFFIC_CHECK_TTL = dt.timedelta(hours=24)


def unverified_low_traffic(item, now=None):
    """True for a row accepted as low traffic without a passed, fresh live caller check.

    The adopted autopilots accepted such rows on run counts alone; under the owner rule of
    30 Sep 2026 that acceptance does not count, so the row is measured again and never closes
    a sub-issue until the live check has passed. With `now`, a check older than
    LOW_TRAFFIC_CHECK_TTL (or without a readable timestamp) is not fresh (V6 #970)."""
    if not isinstance(item, dict) or not item.get("low_traffic"):
        return False
    check = item.get("low_traffic_check")
    if not (isinstance(check, dict) and check.get("ok") is True):
        return True
    if now is None:
        return False
    try:
        checked = parse_utc(check.get("checked_at"))
    except (TypeError, ValueError):
        return True
    return now - checked > LOW_TRAFFIC_CHECK_TTL


def invalidate_unverified_low_traffic(item, now=None):
    """Drop an unverified or stale low-traffic acceptance so the next measurement decides again."""
    if not unverified_low_traffic(item, now):
        return False
    for key in ("met_at", "low_traffic", "observed"):
        item.pop(key, None)
    item["low_traffic_invalidated"] = True
    return True


BILLABLE_CONCLUSIONS = ("success", "failure", "timed_out")
UNFINISHED_STATUSES = ("queued", "in_progress", "requested", "waiting", "pending")
# Usage of a day keeps growing while late jobs are exported. A repository's coverage is judged
# only this long after its newest billable run finished; an operating assumption, deliberately
# conservative against the 30 h between the window end and the billing due time.
BILLING_SETTLE = dt.timedelta(hours=72)
# Bumped whenever the meaning of a coverage confirmation changes; older ones are checked again.
COVERAGE_VERSION = 3


def _run_billable(gh_json, repo, run):
    """success/failure/timed_out bill; a cancelled run bills when one of its jobs started."""
    if run.get("conclusion") in BILLABLE_CONCLUSIONS:
        return True
    if run.get("conclusion") != "cancelled":
        return False
    listing = gh_json(f"repos/{repo}/actions/runs/{run.get('id')}/jobs?filter=all&per_page=100")
    jobs = listing.get("jobs") if isinstance(listing, dict) else None
    if not isinstance(jobs, list) or not all(isinstance(j, dict) for j in jobs):
        return None
    return any(j.get("started_at") and j.get("conclusion") != "skipped" for j in jobs)


def billing_coverage_step(gh_json, repos, usage, after_days, confirmed, has_budget, now):
    """Per in-scope repository, the billing export covers the repository's own billable runs.

    Evidence per repository, never borrowed from another one: no run created in the after
    window is still queued or running; its newest billable run (success, failure, timed_out, or
    a cancelled run in which a job started) finished at least BILLING_SETTLE ago; and its usage
    includes a day on or after that run's creation day. A repository with no billable run has
    no usage to miss, so a quiet window never needs a charge row. With more runs than one page,
    every unfinished status is asked for directly. Confirmations carry COVERAGE_VERSION, are
    recorded in `confirmed` (persisted by the caller) and are not read again, so the check
    spreads over ticks (V6 #970).

    Returns ("ok", None), ("budget", repo) when the tick ran out of calls, ("error", repo) for an
    unreadable or ambiguous listing, or ("pending"|"lagging", [repos]) to retry later."""
    lo, hi = after_days[0], after_days[-1]
    by_repo = {}
    for name, days in usage.items():
        by_repo.setdefault(str(name).lower(), set()).update(days)

    def count(listing):
        total = listing.get("total_count") if isinstance(listing, dict) else None
        return None if isinstance(total, bool) or not isinstance(total, int) else total

    pending, lagging = [], []
    for repo in repos:
        mark = confirmed.get(repo)
        if isinstance(mark, dict) and mark.get("v") == COVERAGE_VERSION:
            continue
        if not has_budget():
            return "budget", repo
        runs = gh_json(f"repos/{repo}/actions/runs?created={lo}..{hi}&per_page=100")
        listed = runs.get("workflow_runs") if isinstance(runs, dict) else None
        total = count(runs)
        if not isinstance(listed, list) or total is None or not all(isinstance(r, dict) for r in listed):
            return "error", repo
        if total > len(listed):
            unfinished = False
            for status in UNFINISHED_STATUSES:
                if not has_budget():
                    return "budget", repo
                probe = count(gh_json(f"repos/{repo}/actions/runs?created={lo}..{hi}&status={status}&per_page=1"))
                if probe is None:
                    return "error", repo
                if probe:
                    unfinished = True
                    break
        else:
            unfinished = any(r.get("status") != "completed" for r in listed)
        if unfinished:
            pending.append(repo)  # its usage is still being produced
            continue
        newest = None
        for run in sorted(listed, key=lambda r: r.get("created_at") or "", reverse=True):
            if not has_budget():
                return "budget", repo
            billable = _run_billable(gh_json, repo, run)
            if billable is None:
                return "error", repo
            if billable:
                newest = run
                break
        if newest is None:
            if total > len(listed):
                return "error", repo  # the newest page holds no billable run: cannot tell
            confirmed[repo] = {"v": COVERAGE_VERSION, "last": None}
            continue
        try:
            finished = parse_utc(newest.get("updated_at") or newest.get("created_at"))
        except ValueError:
            return "error", repo
        last = (newest.get("created_at") or "")[:10]
        if now - finished < BILLING_SETTLE:
            pending.append(repo)  # the day of its newest run may still be growing
        elif any(day >= last for day in by_repo.get(repo.lower(), ())):
            confirmed[repo] = {"v": COVERAGE_VERSION, "last": last}
        else:
            lagging.append(repo)
    if pending:
        return "pending", pending + lagging
    return ("lagging", lagging) if lagging else ("ok", None)


def close_issue_done(gh, gh_json, board, repo, number, done_option):
    """Close an issue as completed and set its board Status to Done.

    True only when the issue is read back closed and the board mutation succeeded; any other
    outcome leaves the caller's record untouched so the next tick retries (V6 #970)."""
    try:
        issue = gh_json(f"repos/{repo}/issues/{number}")
        if not isinstance(issue, dict) or issue.get("state") not in ("open", "closed"):
            return False
        if issue["state"] == "open":
            code, _, _ = gh("issue", "close", str(number), "-R", repo, "--reason", "completed")
            if code != 0:
                return False
            issue = gh_json(f"repos/{repo}/issues/{number}")
            if not isinstance(issue, dict) or issue.get("state") != "closed":
                return False
        return board(number, done_option) is True
    except Exception:  # noqa: BLE001 - an unexpected shape must not look like a closed issue
        return False


def exception_row(item):
    exception = item.get("owner_exception") or {}
    contexts = ", ".join(f"`{c}`" for c in exception.get("required_contexts") or [])
    return (f"| {item.get('repo', '?')} | Výjimka ownera: PR {exception.get('pr')} zavřen bez merge, "
            f"{contexts} zůstává povinný, úspora se nepočítá |")


def exception_notes(items, sub):
    """One note per distinct owner decision (two #895 rows share one decision)."""
    seen, notes = set(), []
    for item in items:
        exception = item.get("owner_exception") or {}
        key = (exception.get("text"), exception.get("basis"))
        if key in seen:
            continue
        seen.add(key)
        tail = ("Měření pokračuje; až doslovné kritérium (≥ 5 kvalifikovaných běhů) dojde, "
                "doplním jeden komentář." if str(sub) == "892" else
                ("Při uzavření živě ověřeno: " + str((item.get("owner_exception_check") or {}).get("reason")) + "."
                 if (item.get("owner_exception_check") or {}).get("reason") else "Živé ověření bez zaznamenaného důvodu."))
        notes.append(f"\n\n**Výjimka ownera** ({exception.get('decided_at_prague', '')}): "
                     f"„{exception.get('text', '')}“. {exception.get('basis', '')} {tail}")
    return "".join(notes)


def live_children_done(gh_json, gh_graphql, epic_repo, epic, project_id, done_option):
    """Return-based contract: unexpected shapes are (False, reason), never an exception."""
    try:
        return _live_children_done(gh_json, gh_graphql, epic_repo, epic, project_id, done_option)
    except Exception as error:  # noqa: BLE001
        return False, f"verification error: {type(error).__name__}"


def _live_children_done(gh_json, gh_graphql, epic_repo, epic, project_id, done_option):
    if not all(isinstance(v, str) and v for v in (epic_repo, project_id, done_option)):
        return False, "EPIC repository, Project or Done option missing"
    """Every live native sub-issue is closed and Done on the EPIC's Project. The Project is read
    from its own side, every page (standard v1.3.1: Issue.projectItems is only a same-org
    shortcut), and children are matched by repository and number. Returns (ok, reason)."""
    children = []
    for page in range(1, 11):
        rows = gh_json(f"repos/{epic_repo}/issues/{epic}/sub_issues?per_page=100&page={page}")
        if not isinstance(rows, list):
            return False, "sub-issues unreadable"
        for row in rows:
            match = re.search(r"/repos/([^/]+/[^/]+)$", str((row or {}).get("repository_url", "")))
            if not isinstance(row, dict) or not isinstance(row.get("number"), int) or not match:
                return False, "malformed sub-issue"
            if row.get("state") != "closed":
                return False, f"sub-issue {match.group(1)}#{row['number']} is open"
            children.append((match.group(1), row["number"]))
        if len(rows) < 100:
            break
    else:
        return False, "too many sub-issue pages"
    if not children:
        return False, "no sub-issues"
    status, cursor = {}, None
    for _ in range(20):
        after = f", after: {json.dumps(cursor)}" if cursor else ""
        data = gh_graphql(
            f'query {{ node(id: {json.dumps(project_id)}) {{ ... on ProjectV2 {{ items(first: 100{after}) {{ '
            "pageInfo { hasNextPage endCursor } nodes { content { ... on Issue { number "
            "repository { nameWithOwner } } } fieldValueByName(name: \"Status\") { "
            "... on ProjectV2ItemFieldSingleSelectValue { optionId } } } } } } }")
        page = (((data or {}).get("node") or {}).get("items"))
        if not isinstance(page, dict) or not isinstance(page.get("nodes"), list):
            return False, "Project items unreadable"
        for node in page["nodes"]:
            content = (node or {}).get("content") or {}
            key = ((content.get("repository") or {}).get("nameWithOwner"), content.get("number"))
            status.setdefault(key, []).append(((node.get("fieldValueByName") or {}).get("optionId")))
        info = page.get("pageInfo")
        if not isinstance(info, dict) or not isinstance(info.get("hasNextPage"), bool):
            return False, "Project pagination unreadable"  # never read a missing flag as the last page
        if not info["hasNextPage"]:
            break
        cursor = info.get("endCursor")
        if not isinstance(cursor, str) or not cursor:
            return False, "Project pagination broken"
    else:
        return False, "too many Project pages"
    for key in children:
        if status.get(key) != [done_option]:
            return False, f"sub-issue {key[0]}#{key[1]} is not Done exactly once on the Project"
    return True, f"{len(children)} sub-issues closed and Done"
