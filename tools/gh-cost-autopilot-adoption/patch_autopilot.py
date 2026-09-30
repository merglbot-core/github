"""Anchored patches for the adopted #888/#910 autopilots (owner plan of 30 Sep 2026).

Each change is an exact-string replacement that must match exactly once in the adopted
source; a fully patched source is returned unchanged; anything in between is drift and
fails closed. The adopted inputs are the live files with sha256 4b78d530... (888) and
08477d87... (910); see ../README.md and adopted/*.diff for how they came to be.
"""

REPLACEMENTS_888 = [
    # Owner rule 8 (V6 #970): the low-traffic branch needs the complete run census.
    ('''    runs = gh_json(f"repos/{repo}/actions/workflows/{workflow}/runs"
                   f"?event=pull_request&created=%3E%3D{since[:10]}&per_page=30")
    if runs is None:
        return False
    known = item.setdefault("runs", {})
    for run in runs.get("workflow_runs", []):
        run_id = str(run.get("id"))
        if run_id in known or run.get("status") != "completed" or run.get("created_at", "") < since:
            continue
        if CALLS["n"] > MAX_CALLS_PER_TICK - 3:
            break
        jobs = (gh_json(f"repos/{repo}/actions/runs/{run_id}/jobs?per_page=20") or {}).get("jobs", [])
''', '''    runs = gh_json(f"repos/{repo}/actions/workflows/{workflow}/runs"
                   f"?event=pull_request&created=%3E%3D{since[:10]}&per_page=100")
    if not isinstance(runs, dict):
        return False
    known = item.setdefault("runs", {})
    listed = runs.get("workflow_runs")
    total = runs.get("total_count")
    # The low-traffic branch below decides on the absence of bad runs, so it needs the complete
    # census: a well-formed listing of every run, each finished and read (owner rule 30 Sep 2026,
    # V6 #970). A malformed listing is never read as an empty one.
    malformed = not isinstance(listed, list) or not all(isinstance(r, dict) for r in listed)
    incomplete = (malformed or not isinstance(total, int) or isinstance(total, bool)
                  or total > len(listed))
    # A busy repository still accumulates evidence from the listed runs; only the low-traffic
    # acceptance, which needs the whole census, stays blocked.
    for run in [] if malformed else listed:
        run_id = str(run.get("id"))
        if run.get("created_at", "") < since:
            continue
        if run.get("status") != "completed":
            # Checked before the cache: a measured run that is being re-run is unfinished again.
            incomplete = True
            continue
        attempts = item.setdefault("attempts", {})
        attempt = run.get("run_attempt") or 1
        if run_id in known and attempts.get(run_id, 1) == attempt:
            continue
        # A re-run that finished between sweeps is measured again from its current attempt.
        known.pop(run_id, None)
        attempts[run_id] = attempt
        if CALLS["n"] > MAX_CALLS_PER_TICK - 3:
            incomplete = True
            break
        listing = gh_json(f"repos/{repo}/actions/runs/{run_id}/jobs?per_page=100")
        jobs = listing.get("jobs") if isinstance(listing, dict) else None
        count = listing.get("total_count") if isinstance(listing, dict) else None
        if not isinstance(jobs, list) or not isinstance(count, int) or isinstance(count, bool) or count > len(jobs):
            incomplete = True
            continue
'''),
    # Owner rule 8: a low-traffic row counts only with its caller verified live on main.
    ('''    over = [r for r in known.values() if r["job_s"] > DOD_MAX_SECONDS or r.get("jobs", 1) != 1]
    if not over and now() - parse(since) >= dt.timedelta(days=LOW_TRAFFIC_GRACE_DAYS):
        item.update(met_at=iso(), low_traffic=True, observed=len(known))
''', '''    over = [r for r in known.values() if r["job_s"] > DOD_MAX_SECONDS or r.get("jobs", 1) != 1]
    item["census_incomplete"] = incomplete
    if not over and not incomplete and now() - parse(since) >= dt.timedelta(days=LOW_TRAFFIC_GRACE_DAYS):
        # Owner rule 30 Sep 2026: a low-traffic row counts only with its caller verified live on main.
        from cost_adoption import live_caller_config
        evidence = live_caller_config(gh_json, repo, workflow, state.get("prs", {}).get(item.get("since_pr")))
        item["low_traffic_check"] = dict(evidence, checked_at=iso())
        if not evidence.get("ok"):
            log(f"low-traffic acceptance withheld for #{item['sub']} {repo}: {evidence.get('reason')}")
            return True
        item.update(met_at=iso(), low_traffic=True, observed=len(known))
'''),
    ('''    note = (f" (málo PR provozu: za {LOW_TRAFFIC_GRACE_DAYS} dní po merge {item.get('observed', 0)} běh(ů), "
            f"žádný nad {DOD_MAX_SECONDS} s ani rozdělený; workflow ověřen na main)" if item.get("low_traffic") else "")
''', '''    check = item.get("low_traffic_check") or {}
    note = (f" (výjimka nízkého provozu: za {LOW_TRAFFIC_GRACE_DAYS} dní po merge {item.get('observed', 0)} běh(ů), "
            f"žádný nad {DOD_MAX_SECONDS} s ani rozdělený; caller živě ověřen na main "
            f"`{check.get('main_sha', '')[:8]}`, hub `{check.get('hub_sha', '')[:8]}`)" if item.get("low_traffic") else "")
'''),
    # The #910 multi-tick sweep: the live checks cost calls, so one sweep may span ticks.
    ('''def measure_dod(state):
    due = parse(state.get("run_counting_at") or "2000-01-01T00:00:00Z")
    if now() - due < dt.timedelta(hours=RUN_COUNT_INTERVAL_HOURS):
        return False
    for item_key, item in state.get("dod", {}).items():
        if (item.get("met_at") and (str(item.get("sub")) != "892" or item.get("literal_verified"))) or CALLS["n"] > MAX_CALLS_PER_TICK - 8:
            continue
        phase(f"dod {item_key}", MEASURES[item.get("kind", "job_runs")], state, item)
        item["checked_at"] = iso()
    state["run_counting_at"] = iso()
    save_state(state)
    return True
''', '''def measure_dod(state):
    """One sweep measures every unmet item, spread over as many ticks as the call budget
    needs; `run_counting_at` moves only when the sweep is complete (ported from #910)."""
    due = parse(state.get("run_counting_at") or "2000-01-01T00:00:00Z")
    started = state.get("dod_sweep_started_at")
    if not started:
        if now() - due < dt.timedelta(hours=RUN_COUNT_INTERVAL_HOURS):
            return False
        started = iso()
        state["dod_sweep_started_at"] = started
    from cost_adoption import invalidate_unverified_low_traffic
    incomplete = False
    for item_key, item in state.get("dod", {}).items():
        # A low-traffic acceptance without its live caller check does not count (V6 #970).
        invalidate_unverified_low_traffic(item, now())
        if item.get("met_at") and (str(item.get("sub")) != "892" or item.get("literal_verified")):
            continue
        if (item.get("checked_at") or "") >= started:
            continue
        if CALLS["n"] > MAX_CALLS_PER_TICK - 8:
            incomplete = True
            break
        phase(f"dod {item_key}", MEASURES[item.get("kind", "job_runs")], state, item)
        item["checked_at"] = iso()
    if not incomplete:
        state["run_counting_at"] = iso()
        state["dod_sweep_started_at"] = None
    save_state(state)
    return True
'''),
    # #895 (owner 30 Sep 2026): owner exceptions without a measurement count after a live check.
    ('''        if not items or any(not i.get("met_at") and
                            not (economic_exception and i is state["dod"][economic_exception]) for i in items):
            continue
        if str(sub) == "892" and not all(i.get("literal_verified") or owner_excepted(i) for i in items):
            continue
        owner_exception = str(sub) == "892" and not all(i.get("literal_verified") for i in items)
''', '''        from cost_adoption import exception_notes, exception_row, owner_exception_live_ok, unverified_low_traffic
        pending = [i for i in items if (not i.get("met_at") or unverified_low_traffic(i, now()))
                   and not (economic_exception and i is state["dod"][economic_exception])]
        excepted = []
        if pending and all(owner_excepted(i) for i in pending):
            for i in pending:
                ok, reason = owner_exception_live_ok(gh_json, i)
                i["owner_exception_check"] = {"ok": ok, "reason": reason, "checked_at": iso()}
                if not ok:
                    break
                excepted.append(i)
        if not items or len(excepted) != len(pending):
            continue
        if str(sub) == "892" and not all(i.get("literal_verified") or owner_excepted(i) for i in items):
            continue
        owner_exception = (str(sub) == "892" and not all(i.get("literal_verified") for i in items)) or bool(excepted)
'''),
    ('''                              if economic_exception and i is state["dod"][economic_exception] else dod_row(i)) for i in items)
''', '''                              if economic_exception and i is state["dod"][economic_exception]
                              else exception_row(i) if any(i is e for e in excepted) else dod_row(i)) for i in items)
'''),
    ('''                + ("".join(f"\\n\\n**Výjimka ownera** ({i['owner_exception']['decided_at_prague']}): "
                           f"„{i['owner_exception']['text']}“. {i['owner_exception']['basis']} "
                           "Měření pokračuje; až doslovné kritérium (≥ 5 kvalifikovaných běhů) dojde, "
                           "doplním jeden komentář." for i in items if owner_excepted(i)) if owner_exception else "")
''', '''                + (exception_notes([i for i in items if owner_excepted(i)], sub) if owner_exception else "")
'''),
    ('''        comment_key = ("dod:892:owner-exception" if owner_exception
''', '''        comment_key = (f"dod:{sub}:owner-exception" if owner_exception
'''),
    # Billing verdicts (owner 30 Sep 2026) and an hourly retry for missing usage data.
    ('''def post_billing(state, billing):
    repos = sorted({key.rsplit("#", 1)[0] for key, pr in state["prs"].items() if pr.get("merged_at")})
''', '''def post_billing(state, billing):
    from cost_adoption import billing_retry_blocked
    if billing_retry_blocked(billing, now()):
        return False
    repos = sorted({key.rsplit("#", 1)[0] for key, pr in state["prs"].items() if pr.get("merged_at")})
'''),
    ('''            if data is None:
                log(f"billing usage for {org} {year}-{month} unavailable, retrying next tick")
                return False
''', '''            if data is None:
                from cost_adoption import RETRY_AFTER, billing_data_gap_due, data_gap_body
                log(f"billing usage for {org} {year}-{month} unavailable, retrying in an hour")
                if billing_data_gap_due(billing.get("due_at"), now()):
                    comment(EPIC_REPO, BILLING_SUB, data_gap_body(BILLING_SUB), state, "billing:data-gap")
                billing["retry_after"] = iso(now() + RETRY_AFTER)
                save_state(state)
                return False
'''),
    ('''    verdict = "FAKT" if saved_usd >= 0.8 * model else "DATA_GAP"
''', '''    from cost_adoption import billing_verdict, closes_billing, verdict_note
    verdict = billing_verdict(saved_usd, model)
'''),
    ('''            f"Verdikt: **{verdict}**.\\n\\n"
''', '''            f"Verdikt: **{verdict}**. {verdict_note(verdict)}\\n\\n"
'''),
    ('''    if verdict == "FAKT":
        if not DRY_RUN:
            gh("issue", "close", str(BILLING_SUB), "-R", EPIC_REPO, "--reason", "completed")
        board(BILLING_SUB, STATUS_DONE)
        state["subs"].setdefault(str(BILLING_SUB), {})["board_done_at"] = iso()
        save_state(state)
''', '''    if closes_billing(verdict) and not DRY_RUN:
        from cost_adoption import close_issue_done
        # Recorded only when read back closed and Done; otherwise close_epic retries (V6 #970).
        if close_issue_done(gh, gh_json, board, EPIC_REPO, BILLING_SUB, STATUS_DONE):
            state["subs"].setdefault(str(BILLING_SUB), {})["board_done_at"] = iso()
            save_state(state)
'''),
    # Billing: usage data must cover the window before a verdict (V6 #970).
    ('''    if not billing_numbers_finite(value for row in usage.values() for value in row.values()):
''', '''    # Billing lag leaves missing days at zero (V6 #970). Every in-scope repository needs its own
    # coverage evidence (see billing_coverage_step); confirmations persist in the state, so the
    # check spreads over ticks.
    from cost_adoption import RETRY_AFTER, billing_coverage_step, billing_data_gap_due, data_gap_body
    status, detail = billing_coverage_step(gh_json, repos, usage, billing["after_days"],
                                           billing.setdefault("coverage", {}),
                                           lambda: CALLS["n"] <= MAX_CALLS_PER_TICK - 3, now(),
                                           billing.setdefault("coverage_runs", {}))
    if status == "budget":
        save_state(state)
        return False
    if status != "ok":
        log(f"billing usage incomplete ({status}: {detail}); retrying in an hour")
        if billing_data_gap_due(billing.get("due_at"), now()):
            comment(EPIC_REPO, BILLING_SUB, data_gap_body(BILLING_SUB), state, "billing:data-gap")
        billing["retry_after"] = iso(now() + RETRY_AFTER)
        save_state(state)
        return False
    if not billing_numbers_finite(value for row in usage.values() for value in row.values()):
'''),
    ('''    if not comment(EPIC_REPO, EPIC, body, state, "epic:closed"):
        return False
    if not DRY_RUN:
        gh("issue", "close", str(EPIC), "-R", EPIC_REPO, "--reason", "completed")
    board(EPIC, STATUS_DONE)
    state["closed_at"] = iso()
''', '''    if not (stamped(state, "epic:closed") or comment(EPIC_REPO, EPIC, body, state, "epic:closed")):
        return False
    # closed_at ends every later tick, so it is written only after a confirmed close (V6 #970).
    if DRY_RUN or not close_issue_done(gh, gh_json, board, EPIC_REPO, EPIC, STATUS_DONE):
        return False
    state["closed_at"] = iso()
'''),
    # A live child outside the registry (#909) keeps the EPIC open; checked at most hourly.
    ('''    open_subs = [sub for sub, rec in state.get("subs", {}).items()
                 if not rec.get("board_done_at") and not rec.get("closed_elsewhere")]
    if open_subs:
        return False
    body = ("### EPIC uzavřen\\n\\nVšechny sub-issues mají DoD změřenou na provozu a akceptace "
''', '''    from cost_adoption import close_issue_done, closes_billing
    billing, record = state.get("billing") or {}, state.setdefault("subs", {}).setdefault(str(BILLING_SUB), {})
    if (billing.get("posted_at") and closes_billing(billing.get("verdict")) and not record.get("board_done_at")
            and not DRY_RUN):
        # The billing verdict closed #BILLING_SUB but the close was not confirmed: retry it (V6 #970).
        if not close_issue_done(gh, gh_json, board, EPIC_REPO, BILLING_SUB, STATUS_DONE):
            return False
        record["board_done_at"] = iso()
        save_state(state)
    open_subs = [sub for sub, rec in state.get("subs", {}).items()
                 if not rec.get("board_done_at") and not rec.get("closed_elsewhere")]
    if open_subs:
        return False
    from cost_adoption import RETRY_AFTER, live_children_done, parse_utc
    last = state.get("epic_live_check_at")
    if last and now() - parse_utc(last) < RETRY_AFTER:
        return False
    state["epic_live_check_at"] = iso()
    save_state(state)
    ok, reason = live_children_done(gh_json, gh_graphql, EPIC_REPO, EPIC, PROJECT_ID, STATUS_DONE)
    if not ok:
        log(f"EPIC close withheld: {reason}")
        return False
    body = ("### EPIC uzavřen\\n\\nVšechny sub-issues mají DoD změřenou na provozu a akceptace "
'''),
]

REPLACEMENTS_910 = [
    # Billing close retry before the EPIC close (V6 #970).
    ('''    if any(rec.get("technical_hold") for rec in state.get("subs", {}).values()):
        return False
    open_subs = [sub for sub, rec in state.get("subs", {}).items()
''', '''    from cost_adoption import close_issue_done, closes_billing
    billing, record = state.get("billing") or {}, state.setdefault("subs", {}).setdefault(str(BILLING_SUB), {})
    if (billing.get("posted_at") and closes_billing(billing.get("verdict")) and not record.get("board_done_at")
            and not DRY_RUN):
        # The billing verdict closed #BILLING_SUB but the close was not confirmed: retry it (V6 #970).
        if not close_issue_done(gh, gh_json, board, EPIC_REPO, BILLING_SUB, STATUS_DONE):
            return False
        record["board_done_at"] = iso()
        save_state(state)
    if any(rec.get("technical_hold") for rec in state.get("subs", {}).values()):
        return False
    open_subs = [sub for sub, rec in state.get("subs", {}).items()
'''),
    ('''    if not items or stamped(state, "pilot:917:report") or any(not i.get("verdict") for i in items):
        return False
''', '''    if (not items or stamped(state, "pilot:917:report") or any(not i.get("verdict") for i in items)
            or any(i.get("verdict_v") != PILOT_CENSUS_VERSION for i in items)):
        return False
'''),
    # #917 (V6 #968, #970): the pilot census is complete per UTC day (every status, paginated),
    # the measured runs are a uniform deterministic sample, unfinished runs hold the verdict,
    # failed or partial reads decide nothing, and a census spans ticks. Gate outcomes shorter
    # than PILOT_MIN_RUN_S stay out of the comparison; the stop rule is about time.
    ('''def measure_arm64_pilot(state, item):
    """#917: the pilot job on the arm64 label shows no regression against its own x64 history.

    before = first-attempt completed runs in the PILOT_WINDOW_DAYS before the merge whose job ran
    off the label; after = runs since the merge whose job ran on the label. Durations (job
    started_at -> completed_at, so the ci-pr-delay environment wait is excluded) are compared on
    successful runs: p50 and p95 must stay <= PILOT_MAX_RATIO x. Failures (success vs failure
    conclusions only) may not rise by more than PILOT_FAIL_TOLERANCE. The verdict is written only
    after the window has elapsed; a regression is recorded, never silently discharged."""
    repo, workflow, job, label = item["repo"], item["workflow"], item["job"], item["label"]
    since = item_since(state, item)
    if not since:
        return False
    start = parse(since) - dt.timedelta(days=PILOT_WINDOW_DAYS)
    known = item.setdefault("runs", {})
    for created in (f"{start.strftime('%Y-%m-%d')}..{since[:10]}", f"%3E%3D{since[:10]}"):
        runs = gh_json(f"repos/{repo}/actions/workflows/{workflow}/runs"
                       f"?created={created}&status=completed&per_page={PILOT_SAMPLE}")
        if runs is None:
            return False
        for run in runs.get("workflow_runs", []):
            run_id = str(run.get("id"))
            if run_id in known or run.get("run_attempt", 1) != 1 or not run.get("created_at"):
                continue
            if CALLS["n"] > MAX_CALLS_PER_TICK - 3:
                # The next sweep continues from `known`; nothing is decided on a partial census.
                return True
            jobs = (gh_json(f"repos/{repo}/actions/runs/{run_id}/jobs?per_page=30") or {}).get("jobs", [])
            for j in jobs:
                if (j.get("name") == job or (j.get("name") or "").endswith(" / " + job)) and j.get("completed_at"):
                    known[run_id] = {"created": run["created_at"], "labels": j.get("labels") or [],
                                     "s": job_seconds(j) or 0, "concl": j.get("conclusion")}
                    break

    def stats(rows):
        ok = [r["s"] for r in rows if r["concl"] == "success"]
        fails = [r for r in rows if r["concl"] == "failure"]
        return {"n": len(ok) + len(fails), "ok": len(ok), "fail": len(fails),
                "p50": percentile(ok, 0.5), "p95": percentile(ok, 0.95)}

    before = stats([r for r in known.values() if r["created"] < since and label not in r["labels"]])
    after = stats([r for r in known.values() if r["created"] >= since and label in r["labels"]])
    item["before"], item["after"] = before, after
    if now() < parse(since) + dt.timedelta(days=PILOT_WINDOW_DAYS):
        item["verdict"] = None
        return True
    if before["ok"] < PILOT_MIN_RUNS or after["ok"] < PILOT_MIN_RUNS:
        item["verdict"] = "insufficient_data"
        return True
    fail_rate = lambda s: s["fail"] / s["n"] if s["n"] else 0.0  # noqa: E731
    slower = after["p50"] > PILOT_MAX_RATIO * before["p50"] or after["p95"] > PILOT_MAX_RATIO * before["p95"]
    flakier = fail_rate(after) > fail_rate(before) + PILOT_FAIL_TOLERANCE
    item["verdict"] = "regression" if (slower or flakier) else "pass"
    if item["verdict"] == "pass":
        item["met_at"] = iso()
        log(f"DoD met for #{item['sub']} {repo} {job}: arm64 p50 {after['p50']} s / p95 {after['p95']} s "
            f"vs x64 p50 {before['p50']} s / p95 {before['p95']} s")
    else:
        log(f"#{item['sub']} {repo} {job}: arm64 pilot {item['verdict']}")
    return True
''', '''def measure_arm64_pilot(state, item):
    """#917: the pilot job on the arm64 label shows no regression against its own x64 history.

    Windows: PILOT_WINDOW_DAYS before the merge (job off the label) and after it (job on the
    label), cut into UTC-day slices. Every slice is listed completely: every status, paginated,
    at most PILOT_CENSUS_MAX runs. A slice listed after its end is final and never listed again.
    The population is every run created in the windows, re-runs included: a run is always
    measured by its first attempt, so a re-run (often of a slow or failed attempt) neither
    leaves the population nor replaces its first measurement. The measured runs are a uniform,
    deterministic sample of that population: a run belongs to it when the first 8 hex digits of
    sha256(run id) fall below `sample_rate`, fixed once from the complete before-window census
    so that about PILOT_SAMPLE_TARGET runs per window are read (1.0 = every run). Each sampled
    run costs one jobs read of its first attempt, taken only once that attempt has finished. A
    sampled run whose first attempt or pilot job is still queued or running holds the verdict;
    a failed or partial read decides nothing. A census that needs more calls than one tick
    allows, or a current slice whose pages moved while it was listed, sets `partial_census`,
    and measure_dod continues it on the next tick. Census entries, measurements and the sample
    rate carry PILOT_CENSUS_VERSION; state written under another version (including the runs
    cached by the adopted code) is listed, measured and fixed again (V6 #968, #970).

    Durations are job started_at -> completed_at, so the ci-pr-delay wait is excluded. Runs
    shorter than PILOT_MIN_RUN_S are gate outcomes (V6 rejection, nothing to test since
    infra#3342) and are counted apart. Stop rule (owner, 30 Sep 2026): p50 and p95 on the label
    at most PILOT_MAX_RATIO x the x64 history; failures are reported apart. The verdict is
    written only after the window has elapsed and every sampled run has finished."""
    import hashlib
    repo, workflow, job, label = item["repo"], item["workflow"], item["job"], item["label"]
    since = item_since(state, item)
    if not since:
        return False
    merged = parse(since)
    start = merged - dt.timedelta(days=PILOT_WINDOW_DAYS)
    end = merged + dt.timedelta(days=PILOT_WINDOW_DAYS)
    moment = now()
    known = item.setdefault("runs", {})
    census = item.setdefault("census", {})
    if item.get("verdict_v") != PILOT_CENSUS_VERSION:
        # A verdict written under another census version (the adopted code) is decided again.
        for key in ("verdict", "fail_verdict", "ratio_p50", "ratio_p95", "met_at"):
            item.pop(key, None)
        item["verdict_v"] = PILOT_CENSUS_VERSION

    def partial():
        # Out of calls for this tick: nothing is decided, measure_dod resumes on the next tick.
        item["partial_census"] = True
        return True

    def failed():
        # A failed or partial read must not drop runs from the comparison: decide nothing now.
        item["incomplete_at"] = iso()
        return False

    def slices(lo, hi):
        edge = lo
        while edge < hi:
            midnight = dt.datetime(edge.year, edge.month, edge.day, tzinfo=dt.timezone.utc)
            cut = min(midnight + dt.timedelta(days=1), hi)
            yield edge, cut
            edge = cut

    windows, complete = {"before": [], "after": []}, True
    for side, lo, hi in (("before", start, merged), ("after", merged, end)):
        for a, b in slices(lo, hi):
            if a >= moment:
                complete = False
                break
            entry = census.get(iso(a))
            if not (isinstance(entry, dict) and entry.get("final") and entry.get("v") == PILOT_CENSUS_VERSION):
                listed_at, runs, page = now(), {}, 1
                while True:
                    if CALLS["n"] > MAX_CALLS_PER_TICK - 3:
                        return partial()
                    listing = gh_json(f"repos/{repo}/actions/workflows/{workflow}/runs?created="
                                      f"{iso(a)}..{iso(b - dt.timedelta(seconds=1))}&per_page=100&page={page}")
                    batch = listing.get("workflow_runs") if isinstance(listing, dict) else None
                    total = listing.get("total_count") if isinstance(listing, dict) else None
                    if (not isinstance(batch, list) or not all(isinstance(r, dict) for r in batch)
                            or not isinstance(total, int) or isinstance(total, bool) or total > PILOT_CENSUS_MAX):
                        return failed()
                    runs.update((str(r.get("id")), r) for r in batch)
                    if not batch or page * 100 >= total:
                        break
                    page += 1
                if len(runs) < total:
                    if listed_at < b:
                        return partial()  # new runs moved the pages of a current slice: list it again
                    # A past slice cannot gain runs; a short listing there is a data gap, retried
                    # on the next sweep instead of holding every tick (V6 #970).
                    item["census_gap"] = iso(a)
                    return failed()
                # Re-runs stay in the population; their first attempt has finished when a later
                # attempt exists.
                # An hour's margin: a run created just before the slice end may be listed late.
                entry = {"v": PILOT_CENSUS_VERSION, "final": listed_at >= b + dt.timedelta(hours=1),
                         "listed_at": iso(listed_at),
                         "total": total,
                         "runs": {rid: {"created": r["created_at"],
                                        "done": r.get("status") == "completed" or (r.get("run_attempt") or 1) > 1}
                                  for rid, r in runs.items()
                                  if r.get("created_at") and iso(a) <= r["created_at"] < iso(b)}}
                census[iso(a)] = entry
            complete = complete and entry["final"]
            windows[side].extend(entry["runs"].items())

    rate = item.get("sample_rate")
    if (isinstance(rate, bool) or not isinstance(rate, (int, float)) or not 0 < rate <= 1
            or item.get("sample_rate_v") != PILOT_CENSUS_VERSION):
        # The before window lies wholly in the past, so its census is complete here.
        population = len(windows["before"])
        rate = min(1.0, PILOT_SAMPLE_TARGET / population) if population else 1.0
        item["sample_rate"], item["sample_rate_v"] = rate, PILOT_CENSUS_VERSION
    chosen = {side: [(rid, meta) for rid, meta in rows
                     if int(hashlib.sha256(rid.encode()).hexdigest()[:8], 16) < rate * 0x100000000]
              for side, rows in windows.items()}

    pending = []
    for side in ("before", "after"):
        for rid, meta in chosen[side]:
            if (known.get(rid) or {}).get("v") == PILOT_CENSUS_VERSION:
                continue
            if CALLS["n"] > MAX_CALLS_PER_TICK - 3:
                return partial()
            run_done = meta["done"]
            if not run_done:
                fresh = gh_json(f"repos/{repo}/actions/runs/{rid}")
                if not isinstance(fresh, dict):
                    return failed()
                run_done = fresh.get("status") == "completed" or (fresh.get("run_attempt") or 1) > 1
            if not run_done:
                # The first attempt is still queued or running: nothing of it is cached yet, even a
                # finished pilot job, so the run stays retryable and holds the verdict (V6 #970).
                pending.append(rid)
                continue
            # Always the first attempt: a re-run keeps its place in the population and is measured
            # by the attempt that belongs to the window, never by a later one.
            listing = gh_json(f"repos/{repo}/actions/runs/{rid}/attempts/1/jobs?per_page=100")
            jobs = listing.get("jobs") if isinstance(listing, dict) else None
            total = listing.get("total_count") if isinstance(listing, dict) else None
            if (not isinstance(jobs, list) or not all(isinstance(j, dict) for j in jobs)
                    or not isinstance(total, int) or isinstance(total, bool) or total > len(jobs)):
                return failed()
            matching = [j for j in jobs if j.get("name") == job or (j.get("name") or "").endswith(" / " + job)]
            finished = [j for j in matching if j.get("status") == "completed" and j.get("completed_at")]
            if finished:
                j = finished[0]
                known[rid] = {"v": PILOT_CENSUS_VERSION, "created": meta["created"], "labels": j.get("labels") or [],
                              "s": job_seconds(j) or 0, "concl": j.get("conclusion")}
            elif matching:
                pending.append(rid)  # the job is not finished yet: stays retryable and holds the verdict
            else:
                # A finished run without the job (filtered out): remembered, never measured.
                known[rid] = {"v": PILOT_CENSUS_VERSION, "created": meta["created"], "labels": [], "s": 0,
                              "concl": "absent"}
    item.pop("incomplete_at", None)  # every read of this sweep succeeded

    def stats(rows):
        # A timed-out job is a failure; failures shorter than PILOT_MIN_RUN_S are reported apart
        # (a V6 rejection and a fast arm64 breakage look alike by duration).
        rows = [r for r in rows if r["concl"] in ("success", "failure", "timed_out")]
        real = [r for r in rows if r["s"] >= PILOT_MIN_RUN_S]
        ok = [r["s"] for r in real if r["concl"] == "success"]
        fails = [r for r in real if r["concl"] != "success"]
        short = [r for r in rows if r["s"] < PILOT_MIN_RUN_S]
        return {"n": len(ok) + len(fails), "ok": len(ok), "fail": len(fails), "short": len(short),
                "short_fail": len([r for r in short if r["concl"] != "success"]),
                "p50": percentile(ok, 0.5), "p95": percentile(ok, 0.95)}

    measured = {rid: row for rid, row in known.items() if row.get("v") == PILOT_CENSUS_VERSION}
    before = stats([measured[rid] for rid, _ in chosen["before"]
                    if rid in measured and label not in measured[rid]["labels"]])
    after = stats([measured[rid] for rid, _ in chosen["after"]
                   if rid in measured and label in measured[rid]["labels"]])
    item["before"], item["after"], item["pending_runs"] = before, after, sorted(pending)
    item["sample"] = {side: [len(chosen[side]), len(windows[side])] for side in windows}
    if moment < end or not complete or pending:
        item["verdict"] = None
        return True
    if before["ok"] < PILOT_MIN_RUNS or after["ok"] < PILOT_MIN_RUNS:
        item["verdict"] = "insufficient_data"
        return True
    fail_rate = lambda s: s["fail"] / s["n"] if s["n"] else 0.0  # noqa: E731
    slower = after["p50"] > PILOT_MAX_RATIO * before["p50"] or after["p95"] > PILOT_MAX_RATIO * before["p95"]
    flakier = fail_rate(after) > fail_rate(before) + PILOT_FAIL_TOLERANCE
    item["ratio_p50"] = round(after["p50"] / before["p50"], 3) if before["p50"] else None
    item["ratio_p95"] = round(after["p95"] / before["p95"], 3) if before["p95"] else None
    # Owner decision 30 Sep 2026: the stop rule is about time; failures are reported apart.
    item["fail_verdict"] = "elevated" if flakier else "ok"
    item["verdict"] = "regression" if slower else "pass"
    if item["verdict"] == "pass":
        item["met_at"] = iso()
        log(f"DoD met for #{item['sub']} {repo} {job}: arm64 p50 {after['p50']} s / p95 {after['p95']} s "
            f"vs x64 p50 {before['p50']} s / p95 {before['p95']} s")
    else:
        log(f"#{item['sub']} {repo} {job}: arm64 pilot {item['verdict']}")
    return True
'''),
    ('''PILOT_SAMPLE = 30
''', '''PILOT_SAMPLE = 30
# Shorter pilot runs are gate outcomes (V6 rejection, nothing to test), see measure_arm64_pilot.
PILOT_MIN_RUN_S = 60
# The runs API lists at most 1 000 runs per filtered query; a UTC-day slice above it is not listable.
PILOT_CENSUS_MAX = 1000
# About this many runs per window are measured (one jobs read each); smaller windows are read whole.
PILOT_SAMPLE_TARGET = 300
# Bumped whenever the census population changes meaning; older census state is listed again.
PILOT_CENSUS_VERSION = 2
'''),
    # Owner rule 8 (V6 #970): 910 measure_job_runs gets the same census and live check as 888.
    ('''    runs = gh_json(f"repos/{repo}/actions/workflows/{workflow}/runs"
                   f"?event=pull_request&created=%3E%3D{since[:10]}&per_page=30")
    if runs is None:
        return False
    known = item.setdefault("runs", {})
    for run in runs.get("workflow_runs", []):
        run_id = str(run.get("id"))
        if run_id in known or run.get("status") != "completed" or run.get("created_at", "") < since:
            continue
        if CALLS["n"] > MAX_CALLS_PER_TICK - 3:
            break
        jobs = (gh_json(f"repos/{repo}/actions/runs/{run_id}/jobs?per_page=20") or {}).get("jobs", [])
''', '''    runs = gh_json(f"repos/{repo}/actions/workflows/{workflow}/runs"
                   f"?event=pull_request&created=%3E%3D{since[:10]}&per_page=100")
    if not isinstance(runs, dict):
        return False
    known = item.setdefault("runs", {})
    listed = runs.get("workflow_runs")
    total = runs.get("total_count")
    # The low-traffic branch below decides on the absence of bad runs, so it needs the complete
    # census: a well-formed listing of every run, each finished and read (owner rule 30 Sep 2026,
    # V6 #970). A malformed listing is never read as an empty one.
    malformed = not isinstance(listed, list) or not all(isinstance(r, dict) for r in listed)
    incomplete = (malformed or not isinstance(total, int) or isinstance(total, bool)
                  or total > len(listed))
    # A busy repository still accumulates evidence from the listed runs; only the low-traffic
    # acceptance, which needs the whole census, stays blocked.
    for run in [] if malformed else listed:
        run_id = str(run.get("id"))
        if run.get("created_at", "") < since:
            continue
        if run.get("status") != "completed":
            # Checked before the cache: a measured run that is being re-run is unfinished again.
            incomplete = True
            continue
        attempts = item.setdefault("attempts", {})
        attempt = run.get("run_attempt") or 1
        if run_id in known and attempts.get(run_id, 1) == attempt:
            continue
        # A re-run that finished between sweeps is measured again from its current attempt.
        known.pop(run_id, None)
        attempts[run_id] = attempt
        if CALLS["n"] > MAX_CALLS_PER_TICK - 3:
            incomplete = True
            break
        listing = gh_json(f"repos/{repo}/actions/runs/{run_id}/jobs?per_page=100")
        jobs = listing.get("jobs") if isinstance(listing, dict) else None
        count = listing.get("total_count") if isinstance(listing, dict) else None
        if not isinstance(jobs, list) or not isinstance(count, int) or isinstance(count, bool) or count > len(jobs):
            incomplete = True
            continue
'''),
    ('''    over = [r for r in known.values() if r["job_s"] > DOD_MAX_SECONDS or r.get("jobs", 1) != 1]
    if not over and now() - parse(since) >= dt.timedelta(days=LOW_TRAFFIC_GRACE_DAYS):
        item.update(met_at=iso(), low_traffic=True, observed=len(known))
''', '''    over = [r for r in known.values() if r["job_s"] > DOD_MAX_SECONDS or r.get("jobs", 1) != 1]
    item["census_incomplete"] = incomplete
    if not over and not incomplete and now() - parse(since) >= dt.timedelta(days=LOW_TRAFFIC_GRACE_DAYS):
        # Owner rule 30 Sep 2026: a low-traffic row counts only with its caller verified live on main.
        from cost_adoption import live_caller_config
        evidence = live_caller_config(gh_json, repo, workflow, state.get("prs", {}).get(item.get("since_pr")))
        item["low_traffic_check"] = dict(evidence, checked_at=iso())
        if not evidence.get("ok"):
            log(f"low-traffic acceptance withheld for #{item['sub']} {repo}: {evidence.get('reason')}")
            return True
        item.update(met_at=iso(), low_traffic=True, observed=len(known))
'''),
    ('''    runs = gh_json(f"repos/{repo}/actions/workflows/{workflow}/runs"
                   f"?created=%3E%3D{since[:10]}&status=completed&per_page=30")
    if runs is None:
        return False
    known = item.setdefault("runs", {})
    for run in runs.get("workflow_runs", []):
        run_id = str(run.get("id"))
        if run_id in known or run.get("created_at", "") < since:
            continue
        if CALLS["n"] > MAX_CALLS_PER_TICK - 3:
            break
        jobs = (gh_json(f"repos/{repo}/actions/runs/{run_id}/jobs?per_page=30") or {}).get("jobs", [])
''', '''    runs = gh_json(f"repos/{repo}/actions/workflows/{workflow}/runs"
                   f"?created=%3E%3D{since[:10]}&per_page=100")
    if not isinstance(runs, dict):
        return False
    known = item.setdefault("runs", {})
    listed = runs.get("workflow_runs")
    total = runs.get("total_count")
    # The low-traffic branch below decides on the absence of bad runs, so it needs the complete
    # census: a well-formed listing of every run, each finished and read (owner rule 30 Sep 2026,
    # V6 #970). A malformed listing is never read as an empty one.
    malformed = not isinstance(listed, list) or not all(isinstance(r, dict) for r in listed)
    incomplete = (malformed or not isinstance(total, int) or isinstance(total, bool)
                  or total > len(listed))
    # A busy repository still accumulates evidence from the listed runs; only the low-traffic
    # acceptance, which needs the whole census, stays blocked.
    for run in [] if malformed else listed:
        run_id = str(run.get("id"))
        if run.get("created_at", "") < since:
            continue
        if run.get("status") != "completed":
            # Checked before the cache: a measured run that is being re-run is unfinished again.
            incomplete = True
            continue
        attempts = item.setdefault("attempts", {})
        attempt = run.get("run_attempt") or 1
        if run_id in known and attempts.get(run_id, 1) == attempt:
            continue
        # A re-run that finished between sweeps is measured again from its current attempt.
        known.pop(run_id, None)
        attempts[run_id] = attempt
        if CALLS["n"] > MAX_CALLS_PER_TICK - 3:
            incomplete = True
            break
        listing = gh_json(f"repos/{repo}/actions/runs/{run_id}/jobs?per_page=100")
        jobs = listing.get("jobs") if isinstance(listing, dict) else None
        count = listing.get("total_count") if isinstance(listing, dict) else None
        if not isinstance(jobs, list) or not isinstance(count, int) or isinstance(count, bool) or count > len(jobs):
            incomplete = True
            continue
'''),
    # #913 (owner 30 Sep 2026): the low-traffic runner row needs the runner verified on main.
    ('''    # new label, green and under the limit; the label itself is proven by the file_state
    # item of the same workflow on main.
    over = [r for r in known.values()
            if label not in r["labels"] or not r["ok"] or r["s"] > item.get("max_s", 300)]
    if not over and now() - parse(since) >= dt.timedelta(days=LOW_TRAFFIC_GRACE_DAYS):
        item.update(met_at=iso(), low_traffic=True, observed=len(known))
''', '''    # new label, green and under the limit, and (owner rule 30 Sep 2026) the caller on main
    # is verified live to run on the label, explicitly or through the pinned hub default.
    over = [r for r in known.values()
            if label not in r["labels"] or not r["ok"] or r["s"] > item.get("max_s", 300)]
    item["census_incomplete"] = incomplete
    if not over and not incomplete and now() - parse(since) >= dt.timedelta(days=LOW_TRAFFIC_GRACE_DAYS):
        from cost_adoption import live_caller_config
        evidence = live_caller_config(gh_json, repo, workflow, state.get("prs", {}).get(item.get("since_pr")),
                                      label=label)
        item["low_traffic_check"] = dict(evidence, checked_at=iso())
        if not evidence.get("ok"):
            log(f"low-traffic acceptance withheld for #{item['sub']} {repo}: {evidence.get('reason')}")
            return True
        item.update(met_at=iso(), low_traffic=True, observed=len(known))
'''),
    ('''        note = (f" (málo provozu: za {LOW_TRAFFIC_GRACE_DAYS} dní po merge {item.get('observed', 0)} běh(ů), "
                "žádný mimo label, červený ani nad limitem; label ověřen na main)" if item.get("low_traffic") else "")
''', '''        check = item.get("low_traffic_check") or {}
        note = (f" (výjimka nízkého provozu: za {LOW_TRAFFIC_GRACE_DAYS} dní po merge {item.get('observed', 0)} "
                f"běh(ů), žádný mimo label, červený ani nad limitem; runner `{check.get('runner', '')}` "
                f"({check.get('runner_source', '')}) živě ověřen na main `{check.get('main_sha', '')[:8]}`)"
                if item.get("low_traffic") else "")
'''),
    ('''        mark = {"pass": "✅", "regression": "❌", "insufficient_data": "⚠️ málo dat"}.get(item.get("verdict"), "⏳")
        return (f"| {item['repo']} | `{item['workflow']}` / {item['job']}: x64 p50 {b.get('p50', '-')} s, "
                f"p95 {b.get('p95', '-')} s, {b.get('fail', 0)}/{b.get('n', 0)} failů → `{item['label']}` "
                f"p50 {a.get('p50', '-')} s, p95 {a.get('p95', '-')} s, {a.get('fail', 0)}/{a.get('n', 0)} failů "
                f"{mark} |")
''', '''        mark = {"pass": "✅", "regression": "❌", "insufficient_data": "⚠️ málo dat"}.get(item.get("verdict"), "⏳")
        fails = " · faily zvýšené" if item.get("fail_verdict") == "elevated" else ""

        def sample(entry, side):
            counts = (entry.get("sample") or {}).get(side) or ["-", "-"]
            return f"{counts[0]}/{counts[1]}"
        return (f"| {item['repo']} | `{item['workflow']}` / {item['job']}: x64 p50 {b.get('p50', '-')} s, "
                f"p95 {b.get('p95', '-')} s, {b.get('fail', 0)}/{b.get('n', 0)} failů → `{item['label']}` "
                f"p50 {a.get('p50', '-')} s ({item.get('ratio_p50', '-')}×), p95 {a.get('p95', '-')} s "
                f"({item.get('ratio_p95', '-')}×), {a.get('fail', 0)}/{a.get('n', 0)} failů; krátké běhy "
                f"mimo srovnání {b.get('short', 0)}/{a.get('short', 0)} (z toho failů {b.get('short_fail', 0)}/"
                f"{a.get('short_fail', 0)}); vzorek {sample(item, 'before')} → "
                f"{sample(item, 'after')} běhů {mark}{fails} |")
'''),
    ('''            + f"\\n\\nKritérium: úspěšné běhy jobu; p50 i p95 na `ubuntu-24.04-arm` ≤ {PILOT_MAX_RATIO}× "
              f"x64 historie {PILOT_WINDOW_DAYS} dní před merge (doba bez čekání ci-pr-delay); podíl "
              f"failů nejvýš +{int(PILOT_FAIL_TOLERANCE * 100)} p. b. Vzorek ≤ {PILOT_SAMPLE} běhů na okno.\\n\\n"
            + ("**Pilot prošel.** Druhá půlka DoD (rollout do 5 workflow) je rozhodnutí ownera: "
               "buď rollout PR, nebo zavřít #917 s odloženým rolloutem. Autopilot issue nezavírá "
               "(technical_hold)."
               if passed else
               "**Regrese nebo málo dat** u označených položek — rozhodnutí ownera (rollback "
               "`runs-on`, nebo prodloužení pilotu). Autopilot issue nezavírá.")
''', '''            + f"\\n\\nKritérium stop pravidla (rozhodnutí ownera 30. 9. 2026): úspěšné běhy jobu, p50 i p95 "
              f"na `ubuntu-24.04-arm` nejvýš {PILOT_MAX_RATIO}× x64 historie {PILOT_WINDOW_DAYS} dní před merge "
              f"(doba bez čekání ci-pr-delay). Běhy kratší než {PILOT_MIN_RUN_S} s jsou výsledky brány "
              "(zamítnutí V6, nic k testování) a do srovnání nepatří. Faily se hlásí zvlášť: zvýšené jsou "
              f"při nárůstu o víc než {int(PILOT_FAIL_TOLERANCE * 100)} p. b. Okna jsou přesně {PILOT_WINDOW_DAYS} "
              f"dní před a po merge, běhy každého dne vypsané úplně; měří se rovnoměrný deterministický "
              f"vzorek (hash ID běhu, cíl ~{PILOT_SAMPLE_TARGET} běhů na okno, menší okna celá) a běžící "
              "běhy verdikt drží.\\n\\n"
            + ("**Časově pilot prošel u všech jobů.** "
               if passed else
               "**Časová regrese nebo málo dat** u označených jobů. ")
            + "Postup podle rozhodnutí ownera z 30. 9.: job nad 1,2× se vrátí na x64, ostatní zůstávají, "
              "infra `terraform-plan-pr.yml` job `plan` se převede na arm64 a týden se ověří. Autopilot "
              "issue nezavírá (technical_hold)."
'''),
    # A low-traffic acceptance without its live caller check does not count (V6 #970).
    ('''        if item.get("met_at") or (item.get("checked_at") or "") >= started:
            continue
''', '''        from cost_adoption import invalidate_unverified_low_traffic
        invalidate_unverified_low_traffic(item, now())
        if (item.get("kind") == "arm64_pilot" and item.get("verdict_v") != PILOT_CENSUS_VERSION
                and (item.get("met_at") or item.get("verdict"))):
            # A pilot verdict written under another census version is measured again (V6 #970).
            for key in ("met_at", "verdict", "fail_verdict", "ratio_p50", "ratio_p95"):
                item.pop(key, None)
        if item.get("met_at") or (item.get("checked_at") or "") >= started:
            continue
'''),
    ('''        if not items or any(not i.get("met_at") for i in items):
            continue
''', '''        from cost_adoption import unverified_low_traffic
        if not items or any(not i.get("met_at") or unverified_low_traffic(i, now()) for i in items):
            continue
'''),
    # #917: a pilot census that ran out of calls continues on the next tick, not 6 h later.
    ('''        phase(f"dod {item_key}", MEASURES[item.get("kind", "job_runs")], state, item)
        item["checked_at"] = iso()
''', '''        phase(f"dod {item_key}", MEASURES[item.get("kind", "job_runs")], state, item)
        if item.pop("partial_census", False):
            incomplete = True
            continue
        item["checked_at"] = iso()
'''),
    # tools/gh-cost-910-closeout anchors on the legacy f-string key; it must now fail closed.
    ('''        key = f"dod:{sub}"
''', '''        # Built without the legacy f-string on purpose: tools/gh-cost-910-closeout anchors on it
        # and must fail closed on this adopted code instead of rewriting close_finished_subs.
        key = "dod:" + str(sub)
'''),
    ('''    repos = sorted({key.rsplit("#", 1)[0] for _, key in billing_inputs(state)}
                   | set(billing.get("extra_repos", [])))
''', '''    from cost_adoption import billing_retry_blocked
    if billing_retry_blocked(billing, now()):
        return False
    repos = sorted({key.rsplit("#", 1)[0] for _, key in billing_inputs(state)}
                   | set(billing.get("extra_repos", [])))
'''),
    ('''            if data is None:
                log(f"billing usage for {org} {year}-{month} unavailable, retrying next tick")
                return False
''', '''            if data is None:
                from cost_adoption import RETRY_AFTER, billing_data_gap_due, data_gap_body
                log(f"billing usage for {org} {year}-{month} unavailable, retrying in an hour")
                if billing_data_gap_due(billing.get("due_at"), now()):
                    comment(EPIC_REPO, BILLING_SUB, data_gap_body(BILLING_SUB), state, "billing:data-gap")
                billing["retry_after"] = iso(now() + RETRY_AFTER)
                save_state(state)
                return False
'''),
    ('''    verdict = "FAKT" if saved_usd >= 0.8 * model else "DATA_GAP"
''', '''    from cost_adoption import billing_verdict, closes_billing, verdict_note
    verdict = billing_verdict(saved_usd, model)
'''),
    ('''            f"Verdikt: **{verdict}**.\\n\\n"
''', '''            f"Verdikt: **{verdict}**. {verdict_note(verdict)}\\n\\n"
'''),
    ('''    if not billing_numbers_finite(value for group in (usage, skus) for row in group.values() for value in row.values()):
''', '''    # Billing lag leaves missing days at zero (V6 #970). Every in-scope repository needs its own
    # coverage evidence (see billing_coverage_step); confirmations persist in the state, so the
    # check spreads over ticks.
    from cost_adoption import RETRY_AFTER, billing_coverage_step, billing_data_gap_due, data_gap_body
    status, detail = billing_coverage_step(gh_json, repos, usage, billing["after_days"],
                                           billing.setdefault("coverage", {}),
                                           lambda: CALLS["n"] <= MAX_CALLS_PER_TICK - 3, now(),
                                           billing.setdefault("coverage_runs", {}))
    if status == "budget":
        save_state(state)
        return False
    if status != "ok":
        log(f"billing usage incomplete ({status}: {detail}); retrying in an hour")
        if billing_data_gap_due(billing.get("due_at"), now()):
            comment(EPIC_REPO, BILLING_SUB, data_gap_body(BILLING_SUB), state, "billing:data-gap")
        billing["retry_after"] = iso(now() + RETRY_AFTER)
        save_state(state)
        return False
    if not billing_numbers_finite(value for group in (usage, skus) for row in group.values() for value in row.values()):
'''),
    ('''    if verdict == "FAKT":
        if not DRY_RUN:
            gh("issue", "close", str(BILLING_SUB), "-R", EPIC_REPO, "--reason", "completed")
        board(BILLING_SUB, STATUS_DONE)
        state["subs"].setdefault(str(BILLING_SUB), {})["board_done_at"] = iso()
        save_state(state)
''', '''    if closes_billing(verdict) and not DRY_RUN:
        from cost_adoption import close_issue_done
        # Recorded only when read back closed and Done; otherwise close_epic retries (V6 #970).
        if close_issue_done(gh, gh_json, board, EPIC_REPO, BILLING_SUB, STATUS_DONE):
            state["subs"].setdefault(str(BILLING_SUB), {})["board_done_at"] = iso()
            save_state(state)
'''),
]


def _apply(source, replacements, name):
    done = [after in source for _, after in replacements]
    if all(done):
        # Fully patched: every patched block exactly once, and no legacy block left beside
        # it (a `before` may legitimately occur inside the `after` texts, V6 #970).
        for before, after in replacements:
            if source.count(after) != 1:
                raise ValueError(f"{name}: duplicated patched block: {after.splitlines()[0].strip()[:80]}")
            if source.count(before) != sum(a.count(before) for _, a in replacements):
                raise ValueError(f"{name}: legacy block beside the patched one: {before.splitlines()[0].strip()[:80]}")
        return source
    if any(done):
        raise ValueError(f"{name}: partially patched source; refusing")
    for before, after in replacements:
        if source.count(before) != 1:
            raise ValueError(f"{name}: anchor drift ({source.count(before)} matches): {before.splitlines()[0].strip()[:80]}")
        source = source.replace(before, after)
    return source


def patch_888(source):
    return _apply(source, REPLACEMENTS_888, "888")


def patch_910(source):
    return _apply(source, REPLACEMENTS_910, "910")
