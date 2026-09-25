"""Prepare a fail-closed patch for the existing EPIC #910 local autopilot.

The patch function is pure. Production installation is a separate, lock-bound
operation after this source has passed PR review.
"""


OLD_NO_PUSH_START = "def measure_no_push_runs(state, item):\n"
OLD_NO_PUSH_END = "\n\ndef measure_file_state(state, item):\n"

NEW_NO_PUSH = '''def measure_no_push_runs(state, item):
    """#914/#915: require current no-push YAML and a complete natural run census."""
    from cost_semantic import main_trigger_contract, push_run_counts
    import base64
    repo, workflow = item["repo"], item["workflow"]
    since = item_since(state, item)
    if not since:
        return False
    branch = gh_json(f"repos/{repo}/branches/main")
    main_sha = (branch or {}).get("commit", {}).get("sha")
    if not main_sha:
        return False
    content = gh_json(f"repos/{repo}/contents/.github/workflows/{workflow}?ref={main_sha}")
    if content is None or not content.get("content"):
        return False
    source = base64.b64decode(content["content"]).decode("utf-8", "replace")
    if not main_trigger_contract(source, item.get("need_schedule", False)):
        item["main_trigger_contract"] = "not_verified"
        return False
    item["main_trigger_contract"] = "verified"

    rows = []
    page = 1
    expected_total = None
    while True:
        if page > 10 or CALLS["n"] > MAX_CALLS_PER_TICK - 8:
            item["pagination_complete"] = False
            return False
        result = gh_json(f"repos/{repo}/actions/workflows/{workflow}/runs"
                         f"?branch=main&created=%3E%3D{since[:10]}&per_page=100&page={page}")
        if result is None or not isinstance(result.get("total_count"), int) or not isinstance(result.get("workflow_runs"), list):
            item["pagination_complete"] = False
            return False
        if expected_total is None:
            expected_total = result["total_count"]
        elif result["total_count"] != expected_total:
            item["pagination_complete"] = False
            return False
        rows.extend(result.get("workflow_runs", []))
        if len(rows) >= expected_total:
            break
        page += 1
    if len(rows) != expected_total:
        item["pagination_complete"] = False
        return False

    known_premerge = set()
    pr = state.get("prs", {}).get(item.get("since_pr"), {})
    merge_sha = pr.get("merge_sha")
    if merge_sha:
        commit = gh_json(f"repos/{repo}/commits/{merge_sha}")
        if commit is None:
            return False
        known_premerge = {parent["sha"] for parent in commit.get("parents", [])
                          if parent.get("sha")}
    measurement = push_run_counts({"total_count": expected_total, "workflow_runs": rows},
                                  since, known_premerge)
    if measurement is None:
        return False
    item["push_runs"] = measurement["post_merge_pushes"]
    item["excluded_premerge_run_ids"] = measurement["excluded_premerge_run_ids"]
    item["schedule_runs"] = measurement["schedule_successes"]
    commits = gh_json(f"repos/{repo}/commits?sha={main_sha}&since={since}&per_page=10")
    if commits is None:
        return False
    current = gh_json(f"repos/{repo}/branches/main")
    if (current or {}).get("commit", {}).get("sha") != main_sha:
        return False
    item["pushes"] = len(commits)
    need_schedule = item.get("need_schedule", False)
    if item["push_runs"] == 0 and item["pushes"] >= 1 and (not need_schedule or item["schedule_runs"] >= 1):
        item["met_at"] = iso()
        log(f"DoD met for #{item['sub']} {repo}: 0 post-config push runs of {workflow}")
    return True
'''


OLD_MISSING = '''    missing = [n for n in item.get("needles", []) if n not in text]
    item["missing"] = missing
    if not missing:
        item["met_at"] = iso()
'''

NEW_MISSING = '''    if item.get("sub") == 913 and repo == "merglbot-core/github" and path == ".github/workflows/pr-gate.yml":
        from cost_semantic import hub_slim_configured
        runner_proof = any(i.get("sub") == 913 and i.get("kind") == "runner_label"
                           and i.get("met_at") for i in state.get("dod", {}).values())
        missing = [] if hub_slim_configured(text) and runner_proof else ["semantic ubuntu-slim + natural runner proof"]
    else:
        missing = [n for n in item.get("needles", []) if n not in text]
    item["missing"] = missing
    if not missing:
        item["met_at"] = iso()
'''


def patch(source):
    """Return patched source; reject drift, duplicate installation or ambiguity."""
    if source.count(OLD_NO_PUSH_START) != 1 or source.count(OLD_NO_PUSH_END) != 1:
        raise ValueError("no-push function boundary drift")
    a = source.index(OLD_NO_PUSH_START)
    b = source.index(OLD_NO_PUSH_END, a)
    old = source[a:b]
    if "per_page=50" not in old or "item[\"push_runs\"]" not in old:
        raise ValueError("no-push implementation drift")
    if source.count(OLD_MISSING) != 1:
        raise ValueError("file-state implementation drift")
    return source[:a] + NEW_NO_PUSH + source[b:]


def patch_both(source):
    source = patch(source)
    return source.replace(OLD_MISSING, NEW_MISSING, 1)
