"""Shared helpers for the #888/#910 autopilots (owner plan of 30 Sep 2026).

Installed next to both autopilots (like cost_semantic.py). Every function receives its
GitHub accessors as arguments so tests can stub them, performs only reads, and fails
closed: an unreadable input returns "not verified" and the autopilot simply retries on
its next sweep. Python 3.9 compatible (the launchd interpreter): no PEP 604 unions, no
match statements, no 3.11 UTC constant, and timestamps go through parse_utc().
"""
import base64
import datetime as dt
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
    """Owner decision 30 Sep 2026: FAKT closes, PARTIAL closes, BEZ ÚSPORY stays open."""
    if saved_usd >= 0.8 * model_usd:
        return "FAKT"
    if saved_usd > 0:
        return "PARTIAL"
    return "BEZ ÚSPORY"


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


def hub_calls(text, hub_path=HUB_PR_GATE):
    """Pinned `uses: merglbot-core/github/<hub_path>@<sha>` lines; commented lines never count."""
    pattern = re.compile(r"(?m)^[ \t]*(?:-[ \t]+)?uses:[ \t]*['\"]?" + re.escape(HUB + "/" + hub_path)
                         + r"@([0-9a-f]{40})['\"]?[ \t]*(?:#.*)?$")
    return pattern.findall(text)


def runs_on_values(text):
    """`runs-on:` values; in a caller that only calls a reusable workflow they sit under `with:`."""
    return re.findall(r"(?m)^[ \t]+runs-on:[ \t]*['\"]?([A-Za-z0-9_.-]+)['\"]?[ \t]*(?:#.*)?$", text)


def input_default(text, name):
    """Default of a workflow_call input, found by indentation (no YAML dependency)."""
    lines = text.splitlines()
    for index, line in enumerate(lines):
        match = re.match(r"^([ \t]*)" + re.escape(name) + r":[ \t]*$", line)
        if not match:
            continue
        indent = len(match.group(1))
        for follow in lines[index + 1:]:
            if not follow.strip() or follow.lstrip().startswith("#"):
                continue
            if len(follow) - len(follow.lstrip()) <= indent:
                break
            default = re.match(r"^[ \t]+default:[ \t]*['\"]?([A-Za-z0-9_.-]+)['\"]?[ \t]*(?:#.*)?$", follow)
            if default:
                return default.group(1)
    return None


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
    calls = hub_calls(text)
    if len(calls) != 1:
        return {"ok": False, "reason": f"expected one pinned hub PR Gate call, found {len(calls)}",
                "main_sha": main}
    evidence = {"ok": True, "reason": "caller verified on main", "main_sha": main,
                "hub_sha": calls[0], "workflow": path}
    if label:
        explicit = sorted(set(runs_on_values(text)))
        if len(explicit) > 1:
            return {"ok": False, "reason": f"several runners {explicit}", "main_sha": main}
        if explicit:
            runner, source = explicit[0], "with.runs-on"
        else:
            hub = _text(gh_json, HUB, HUB_PR_GATE, calls[0])
            runner, source = (input_default(hub, "runs-on") if hub else None), "hub default"
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
    """Every live native sub-issue is closed and Done on the EPIC's Project (read project-side
    per child). Children outside epic_repo fail closed: a cross-org projectItems read returns
    nothing without an error. Returns (ok, reason)."""
    owner, name = epic_repo.split("/")
    children = []
    for page in range(1, 11):
        rows = gh_json(f"repos/{epic_repo}/issues/{epic}/sub_issues?per_page=100&page={page}")
        if not isinstance(rows, list):
            return False, "sub-issues unreadable"
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get("number"), int):
                return False, "malformed sub-issue"
            if not str(row.get("repository_url", "")).endswith("/repos/" + epic_repo):
                return False, f"sub-issue #{row['number']} outside {epic_repo}"
            if row.get("state") != "closed":
                return False, f"sub-issue #{row['number']} is open"
            children.append(row["number"])
        if len(rows) < 100:
            break
    else:
        return False, "too many sub-issue pages"
    if not children:
        return False, "no sub-issues"
    fields = " ".join(
        f"i{n}: issue(number: {n}) {{ projectItems(first: 20) {{ nodes {{ project {{ id }} "
        "fieldValueByName(name: \"Status\") { ... on ProjectV2ItemFieldSingleSelectValue { optionId } } } } }"
        for n in children)
    data = gh_graphql(f'query {{ repository(owner: "{owner}", name: "{name}") {{ {fields} }} }}')
    repository = (data or {}).get("repository")
    if not isinstance(repository, dict):
        return False, "Project status unreadable"
    for number in children:
        nodes = (((repository.get(f"i{number}") or {}).get("projectItems") or {}).get("nodes")) or []
        mine = [node for node in nodes if ((node or {}).get("project") or {}).get("id") == project_id]
        if len(mine) != 1 or ((mine[0].get("fieldValueByName") or {}).get("optionId")) != done_option:
            return False, f"sub-issue #{number} is not Done on the Project"
    return True, f"{len(children)} sub-issues closed and Done"
