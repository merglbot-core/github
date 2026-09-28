def measure_filtered(state, item):
    """#892: a paths-filtered workflow runs on fewer pull requests than were opened."""
    repo = item["repo"]
    since = merged_at(state, item["since_pr"])
    if not since:
        return False
    runs = gh_json(f"repos/{repo}/actions/workflows/{item['workflow']}/runs"
                   f"?event=pull_request&created=%3E%3D{since[:10]}&per_page=100")
    pulls = gh_json(f"repos/{repo}/pulls?state=all&sort=created&direction=desc&per_page=50")
    if runs is None or pulls is None:
        return False
    item["runs_after"] = len([r for r in runs.get("workflow_runs", []) if r.get("created_at", "") >= since])
    item["prs_after"] = len([p for p in pulls if p.get("created_at", "") >= since])
    if item["prs_after"] >= item.get("threshold", DOD_RUNS) and item["runs_after"] < item["prs_after"]:
        item["met_at"] = iso()
        log(f"DoD met for #{item['sub']} {repo}: {item['runs_after']} runs for {item['prs_after']} PRs")
    return True

MEASURES = {}

def measure_dod(state):
    due = parse(state.get("run_counting_at") or "2000-01-01T00:00:00Z")
    if now() - due < dt.timedelta(hours=RUN_COUNT_INTERVAL_HOURS):
        return False
    for item_key, item in state.get("dod", {}).items():
        if item.get("met_at") or CALLS["n"] > MAX_CALLS_PER_TICK - 8:
            continue
        phase(f"dod {item_key}", MEASURES[item.get("kind", "job_runs")], state, item)
        item["checked_at"] = iso()
    state["run_counting_at"] = iso()
    save_state(state)
    return True

def close_finished_subs(state):
    for sub, record in state.get("subs", {}).items():
        if record.get("board_done_at") or record.get("closed_elsewhere"):
            continue
        items = [i for i in state.get("dod", {}).values() if str(i.get("sub")) == str(sub)]
        if not items or any(not i.get("met_at") for i in items):
            continue
        body = ("### DoD změřeno na provozu\n\n| repo | měření |\n|---|---|\n"
                + "\n".join(dod_row(i) for i in items)
                + f"\n\nKritérium: přirozené PR běhy s jedním jobem ≤ {DOD_MAX_SECONDS} s "
                  f"(≥ {DOD_RUNS}, u rep s méně než 5 PR měsíčně ≥ {DOD_RUNS_LOW_TRAFFIC}); u CodeQL "
                  "≥ 1 push na main a ≥ 1 týdenní běh. Účtovaná část DoD (minuty/den z billing API) "
                  f"se uzavírá v akceptaci #{BILLING_SUB}.\n\nZavírám a přepínám na Done.")
        if comment(EPIC_REPO, int(sub), body, state, f"dod:{sub}"):
            if not DRY_RUN:
                gh("issue", "close", str(sub), "-R", EPIC_REPO, "--reason", "completed")
            board(int(sub), STATUS_DONE)
            record["board_done_at"] = iso()
            save_state(state)
            log(f"sub-issue #{sub} closed, board Done")
            return True
    return False
