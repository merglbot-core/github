"""Anchored patches for the adopted #888/#910 autopilots (owner plan of 30 Sep 2026).

Each change is an exact-string replacement that must match exactly once in the adopted
source; a fully patched source is returned unchanged; anything in between is drift and
fails closed. The adopted inputs are the live files with sha256 4b78d530... (888) and
08477d87... (910); see ../README.md and adopted/*.diff for how they came to be.
"""

REPLACEMENTS_888 = [
    # Owner rule 8: a low-traffic row counts only with its caller verified live on main.
    ('''    over = [r for r in known.values() if r["job_s"] > DOD_MAX_SECONDS or r.get("jobs", 1) != 1]
    if not over and now() - parse(since) >= dt.timedelta(days=LOW_TRAFFIC_GRACE_DAYS):
        item.update(met_at=iso(), low_traffic=True, observed=len(known))
''', '''    over = [r for r in known.values() if r["job_s"] > DOD_MAX_SECONDS or r.get("jobs", 1) != 1]
    if not over and now() - parse(since) >= dt.timedelta(days=LOW_TRAFFIC_GRACE_DAYS):
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
    incomplete = False
    for item_key, item in state.get("dod", {}).items():
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
''', '''        from cost_adoption import exception_notes, exception_row, owner_exception_live_ok
        pending = [i for i in items if not i.get("met_at")
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
''', '''    if closes_billing(verdict):
'''),
    # A live child outside the registry (#909) keeps the EPIC open; checked at most hourly.
    ('''    open_subs = [sub for sub, rec in state.get("subs", {}).items()
                 if not rec.get("board_done_at") and not rec.get("closed_elsewhere")]
    if open_subs:
        return False
    body = ("### EPIC uzavřen\\n\\nVšechny sub-issues mají DoD změřenou na provozu a akceptace "
''', '''    open_subs = [sub for sub, rec in state.get("subs", {}).items()
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
    ('''PILOT_SAMPLE = 30
''', '''PILOT_SAMPLE = 30
# Shorter pilot runs are gate outcomes (V6 rejection, nothing to test), see measure_arm64_pilot.
PILOT_MIN_RUN_S = 60
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
    if not over and now() - parse(since) >= dt.timedelta(days=LOW_TRAFFIC_GRACE_DAYS):
        from cost_adoption import live_caller_config
        evidence = live_caller_config(gh_json, repo, workflow, None, label=label)
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
    # #917: gate outcomes stay out of the architecture comparison; the stop rule is about time.
    ('''    def stats(rows):
        ok = [r["s"] for r in rows if r["concl"] == "success"]
        fails = [r for r in rows if r["concl"] == "failure"]
        return {"n": len(ok) + len(fails), "ok": len(ok), "fail": len(fails),
                "p50": percentile(ok, 0.5), "p95": percentile(ok, 0.95)}
''', '''    def stats(rows):
        # Runs shorter than PILOT_MIN_RUN_S are gate outcomes, not suite runs: since infra#3342
        # (29 Sep 2026) a head rejected by V6 fails in seconds and a suite with nothing to test
        # is skipped. They are counted apart so they neither shorten p50/p95 nor raise the
        # failure rate of the architecture comparison.
        real = [r for r in rows if r["s"] >= PILOT_MIN_RUN_S]
        ok = [r["s"] for r in real if r["concl"] == "success"]
        fails = [r for r in real if r["concl"] == "failure"]
        return {"n": len(ok) + len(fails), "ok": len(ok), "fail": len(fails),
                "short": len(rows) - len(real),
                "p50": percentile(ok, 0.5), "p95": percentile(ok, 0.95)}
'''),
    ('''    flakier = fail_rate(after) > fail_rate(before) + PILOT_FAIL_TOLERANCE
    item["verdict"] = "regression" if (slower or flakier) else "pass"
''', '''    flakier = fail_rate(after) > fail_rate(before) + PILOT_FAIL_TOLERANCE
    item["ratio_p50"] = round(after["p50"] / before["p50"], 3) if before["p50"] else None
    item["ratio_p95"] = round(after["p95"] / before["p95"], 3) if before["p95"] else None
    # Owner decision 30 Sep 2026: the stop rule is about time; failures are reported apart.
    item["fail_verdict"] = "elevated" if flakier else "ok"
    item["verdict"] = "regression" if slower else "pass"
'''),
    ('''        mark = {"pass": "✅", "regression": "❌", "insufficient_data": "⚠️ málo dat"}.get(item.get("verdict"), "⏳")
        return (f"| {item['repo']} | `{item['workflow']}` / {item['job']}: x64 p50 {b.get('p50', '-')} s, "
                f"p95 {b.get('p95', '-')} s, {b.get('fail', 0)}/{b.get('n', 0)} failů → `{item['label']}` "
                f"p50 {a.get('p50', '-')} s, p95 {a.get('p95', '-')} s, {a.get('fail', 0)}/{a.get('n', 0)} failů "
                f"{mark} |")
''', '''        mark = {"pass": "✅", "regression": "❌", "insufficient_data": "⚠️ málo dat"}.get(item.get("verdict"), "⏳")
        fails = " · faily zvýšené" if item.get("fail_verdict") == "elevated" else ""
        return (f"| {item['repo']} | `{item['workflow']}` / {item['job']}: x64 p50 {b.get('p50', '-')} s, "
                f"p95 {b.get('p95', '-')} s, {b.get('fail', 0)}/{b.get('n', 0)} failů → `{item['label']}` "
                f"p50 {a.get('p50', '-')} s ({item.get('ratio_p50', '-')}×), p95 {a.get('p95', '-')} s "
                f"({item.get('ratio_p95', '-')}×), {a.get('fail', 0)}/{a.get('n', 0)} failů; krátké běhy "
                f"mimo srovnání {b.get('short', 0)}/{a.get('short', 0)} {mark}{fails} |")
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
              f"při nárůstu o víc než {int(PILOT_FAIL_TOLERANCE * 100)} p. b. Vzorek nejvýš {PILOT_SAMPLE} "
              "běhů na okno.\\n\\n"
            + ("**Časově pilot prošel u všech jobů.** "
               if passed else
               "**Časová regrese nebo málo dat** u označených jobů. ")
            + "Postup podle rozhodnutí ownera z 30. 9.: job nad 1,2× se vrátí na x64, ostatní zůstávají, "
              "infra `terraform-plan-pr.yml` job `plan` se převede na arm64 a týden se ověří. Autopilot "
              "issue nezavírá (technical_hold)."
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
    ('''    if verdict == "FAKT":
''', '''    if closes_billing(verdict):
'''),
]


def _apply(source, replacements, name):
    done = [after in source for _, after in replacements]
    if all(done):
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
