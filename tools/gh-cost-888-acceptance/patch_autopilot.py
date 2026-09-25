"""Narrow, fail-closed #888 DoD repair; installation is separately lock-bound."""


NEW_FILTERED = '''def measure_filtered(state, item):
    """#892 requires five real Terraform workflow successes, not PR Gate runs."""
    from cost_888_literal import qualifying_terraform_runs, qualifying_pr_count, terraform_paths_filtered
    import base64
    repo = item["repo"]
    since = merged_at(state, item["since_pr"])
    if not since:
        return False
    branch = gh_json(f"repos/{repo}/branches/main")
    main_sha = (branch or {}).get("commit", {}).get("sha")
    if not main_sha:
        return False
    workflow = item["workflow"]
    content = gh_json(f"repos/{repo}/contents/.github/workflows/{workflow}?ref={main_sha}")
    if content is None or not content.get("content"):
        return False
    source = base64.b64decode(content["content"]).decode("utf-8", "replace")
    # The path filter is a precondition; a comment or an unrelated job is not evidence.
    if not terraform_paths_filtered(source):
        item["literal_verified"] = False
        item["met_at"] = None
        return True

    rows = []
    total = None
    page = 1
    while True:
        if page > 10 or CALLS["n"] > MAX_CALLS_PER_TICK - 8:
            return False
        result = gh_json(f"repos/{repo}/actions/workflows/{workflow}/runs"
                         f"?event=pull_request&created=%3E%3D{since[:10]}&per_page=100&page={page}")
        if result is None or not isinstance(result.get("total_count"), int) or not isinstance(result.get("workflow_runs"), list):
            return False
        if total is None:
            total = result["total_count"]
        elif result["total_count"] != total:
            return False
        rows.extend(result["workflow_runs"])
        if len(rows) >= total:
            break
        page += 1
    good = qualifying_terraform_runs({"total_count": total, "workflow_runs": rows}, since)
    if good is None:
        return False

    pulls = []
    page = 1
    while True:
        if page > 10 or CALLS["n"] > MAX_CALLS_PER_TICK - 8:
            return False
        part = gh_json(f"repos/{repo}/pulls?state=all&sort=created&direction=desc&per_page=100&page={page}")
        if not isinstance(part, list):
            return False
        pulls.extend(part)
        if len(part) < 100 or any(p.get("created_at", "") <= since for p in part):
            break
        page += 1
    prs = qualifying_pr_count(pulls, since)
    if prs is None:
        return False
    current = gh_json(f"repos/{repo}/branches/main")
    if (current or {}).get("commit", {}).get("sha") != main_sha:
        return False
    item["runs_after"], item["prs_after"] = good, prs
    item["literal_verified"] = good >= 5 and good < prs
    item["met_at"] = iso() if item["literal_verified"] else None
    return True
'''


OLD_MEASURE_SKIP = '''        if item.get("met_at") or CALLS["n"] > MAX_CALLS_PER_TICK - 8:
            continue
'''
NEW_MEASURE_SKIP = '''        if (item.get("met_at") and item.get("sub") != 892) or CALLS["n"] > MAX_CALLS_PER_TICK - 8:
            continue
'''

OLD_CLOSE_START = '''        if record.get("board_done_at") or record.get("closed_elsewhere"):
            continue
        items = [i for i in state.get("dod", {}).values() if str(i.get("sub")) == str(sub)]
        if not items or any(not i.get("met_at") for i in items):
            continue
'''

NEW_CLOSE_START = '''        items = [i for i in state.get("dod", {}).values() if str(i.get("sub")) == str(sub)]
        if str(sub) == "892" and record.get("board_done_at") and not all(i.get("literal_verified") for i in items):
            if not DRY_RUN:
                code, _, _ = gh("issue", "reopen", "892", "-R", EPIC_REPO)
                if code != 0:
                    return False
            if not DRY_RUN and not board(892, STATUS_IN_PROGRESS):
                return False
            record["board_done_at"] = None
            record["closed_elsewhere"] = False
            save_state(state)
            log("#892 reopened: literal five-run DoD not verified")
            return True
        if record.get("board_done_at") or record.get("closed_elsewhere"):
            continue
        economic_exception = None
        if str(sub) == "889":
            exception_key = "889|merglbot-proteinaco/acquisition-analysis"
            excluded = state.get("dod", {}).get(exception_key)
            others = [i for i in items if i is not excluded]
            if excluded and not excluded.get("met_at") and all(i.get("met_at") for i in others):
                pr = gh_json("repos/merglbot-proteinaco/acquisition-analysis/pulls/184")
                if pr is None or pr.get("state") != "closed" or pr.get("merged"):
                    return False
                bp = gh_json("repos/merglbot-proteinaco/acquisition-analysis/branches/main/protection")
                if bp is None:
                    return False
                required = {check.get("context") for check in
                            (bp.get("required_status_checks") or {}).get("checks", [])}
                if not {"gitleaks", "dependency-review", "Merglbot PR Assistant v6"} <= required:
                    return False
                economic_exception = exception_key
        if not items or any(not i.get("met_at") and
                            not (economic_exception and i is state["dod"][economic_exception]) for i in items):
            continue
        if str(sub) == "892" and not all(i.get("literal_verified") for i in items):
            continue
'''

OLD_BODY = '''        body = ("### DoD změřeno na provozu\\n\\n| repo | měření |\\n|---|---|\\n"
                + "\\n".join(dod_row(i) for i in items)
'''
NEW_BODY = '''        title = "### Provozní DoD s ekonomickou výjimkou" if economic_exception else "### DoD změřeno na provozu"
        body = (title + "\\n\\n| repo | měření |\\n|---|---|\\n"
                + "\\n".join(("| merglbot-proteinaco/acquisition-analysis | Ekonomická výjimka: PR #184 zavřen bez merge; původní required security kontexty zachovány, úspora se nepočítá |"
                              if economic_exception and i is state["dod"][economic_exception] else dod_row(i)) for i in items)
'''


def patch(source):
    a = source.index("def measure_filtered(state, item):\n")
    b = source.index("\n\nMEASURES =", a)
    old = source[a:b]
    if "per_page=100" not in old or "item[\"runs_after\"]" not in old:
        raise ValueError("filtered verifier drift")
    source = source[:a] + NEW_FILTERED + source[b:]
    for before, after in [(OLD_MEASURE_SKIP, NEW_MEASURE_SKIP),
                          (OLD_CLOSE_START, NEW_CLOSE_START),
                          (OLD_BODY, NEW_BODY)]:
        if source.count(before) != 1:
            raise ValueError("autopilot closeout drift")
        source = source.replace(before, after, 1)
    return source
