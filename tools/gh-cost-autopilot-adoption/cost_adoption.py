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


def billing_verdict(saved_usd, model_usd):
    """Owner decision 30 Sep 2026: FAKT closes, PARTIAL closes, BEZ ÚSPORY stays open.
    A non-positive saving never closes, whatever the model; non-finite input is a data gap (V6 #969)."""
    try:
        finite = math.isfinite(saved_usd) and math.isfinite(model_usd)
    except TypeError:
        finite = False
    if not finite:
        return "DATA_GAP"
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
    """Missing billing data is retried hourly instead of every tick."""
    stamp = billing.get("retry_after")
    return bool(stamp) and now < parse_utc(stamp)


def billing_data_gap_due(due_at, now):
    """72 hours after the due time without usable data, one DATA_GAP comment is written."""
    return bool(due_at) and now - parse_utc(due_at) >= DATA_GAP_AFTER


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
    Callers count all of them, so an extra unpinned call cannot hide (V6 #969)."""
    prefix = HUB + "/" + hub_path + "@"
    found = []
    for job_id, job in doc["jobs"].items():
        uses = job.get("uses") if isinstance(job, dict) else None
        if isinstance(uses, str) and uses.strip().startswith(prefix):
            found.append((job_id, uses.strip()[len(prefix):], job))
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
    branches = spec.get("branches") if isinstance(spec, dict) else None
    ignored = spec.get("branches-ignore") if isinstance(spec, dict) else None
    if branches is not None and not any(b in ("main", "*", "**") for b in branches or []):
        return False
    if ignored and "main" in ignored:
        return False
    return job.get("if") not in (False, "false", "${{ false }}")


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
    record = pr_record or {}
    expected = [p for p in record.get("expected_files") or [] if p.startswith(".github/workflows/")]
    deleted = [p for p in record.get("deleted_files") or [] if p.startswith(".github/workflows/")]
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
        return {"ok": False, "reason": "caller does not run on pull requests to main", "main_sha": main}
    evidence = {"ok": True, "reason": "caller verified on main", "main_sha": main,
                "hub_sha": hub_sha, "job": job_id, "workflow": path}
    if label:
        # Only the hub-calling job's own `with.runs-on` counts; an expression is unresolved
        # and fails closed; without the input the pinned hub default applies. Either way the
        # pinned hub workflow decides the effective runner (V6 #969).
        inputs = job.get("with") if isinstance(job.get("with"), dict) else {}
        hub = _text(gh_json, HUB, HUB_PR_GATE, hub_sha)
        if hub is None:
            return {"ok": False, "reason": "pinned hub workflow unreadable", "main_sha": main}
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
    """#895 rows: the rollout PR stayed closed unmerged and the recorded gitleaks contexts are
    still required on main. Returns (ok, reason)."""
    exception = item.get("owner_exception") or {}
    key, contexts = exception.get("pr"), exception.get("required_contexts") or []
    if not isinstance(key, str) or "#" not in key or not contexts:
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


def exception_row(item):
    exception = item.get("owner_exception") or {}
    contexts = ", ".join(f"`{c}`" for c in exception.get("required_contexts") or [])
    return (f"| {item['repo']} | Výjimka ownera: PR {exception.get('pr')} zavřen bez merge, "
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
                "Při uzavření živě ověřeno: " + (item.get("owner_exception_check") or {}).get("reason", "") + ".")
        notes.append(f"\n\n**Výjimka ownera** ({exception.get('decided_at_prague', '')}): "
                     f"„{exception.get('text', '')}“. {exception.get('basis', '')} {tail}")
    return "".join(notes)


def live_children_done(gh_json, gh_graphql, epic_repo, epic, project_id, done_option):
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
        info = page.get("pageInfo") or {}
        if not info.get("hasNextPage"):
            break
        cursor = info.get("endCursor")
        if not cursor:
            return False, "Project pagination broken"
    else:
        return False, "too many Project pages"
    for key in children:
        if status.get(key) != [done_option]:
            return False, f"sub-issue {key[0]}#{key[1]} is not Done exactly once on the Project"
    return True, f"{len(children)} sub-issues closed and Done"
