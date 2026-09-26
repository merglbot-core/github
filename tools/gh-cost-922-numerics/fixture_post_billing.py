"""Existing gross evaluator, exercised only with sandbox I/O in tests."""

def post_billing(state, billing):
    """#922: billed Actions USD/day per repository (gross amount, so 1-core ubuntu-slim at
    0.002, arm64 at 0.005 and 2-core at 0.006 are priced correctly), 14 days before vs after."""
    repos = sorted({key.rsplit("#", 1)[0] for _, key in billing_inputs(state)}
                   | set(billing.get("extra_repos", [])))
    orgs = sorted({repo.split("/")[0] for repo in repos})
    days = billing["before_days"] + billing["after_days"]
    months = sorted({(int(d[:4]), int(d[5:7])) for d in days})
    usage, skus = {}, {}
    for org in orgs:
        for year, month in months:
            data = gh_json(f"organizations/{org}/settings/billing/usage?year={year}&month={month}")
            if data is None:
                log(f"billing usage for {org} {year}-{month} unavailable, retrying next tick")
                return False
            for item in data.get("usageItems", []):
                sku = item.get("sku") or ""
                if (item.get("product") or "").lower() != "actions" or "storage" in sku.lower():
                    continue
                repo = f"{item.get('organizationName')}/{item.get('repositoryName')}"
                day = (item.get("date") or "")[:10]
                # Count an item only under the month it was requested for, so an overlapping
                # or repeated response can never double a day.
                if day[:7] != f"{year:04d}-{month:02d}":
                    continue
                usage.setdefault(repo, {}).setdefault(day, 0.0)
                usage[repo][day] += item.get("grossAmount") or 0
                side = "before" if day in billing["before_days"] else "after" if day in billing["after_days"] else None
                if side:
                    skus.setdefault(sku, {"before": 0.0, "after": 0.0})[side] += item.get("quantity") or 0
    BILLING_DIR.mkdir(parents=True, exist_ok=True)
    (BILLING_DIR / "acceptance.json").write_text(json.dumps({"usd": usage, "sku_minutes": skus}, indent=1))
    rows, total_before, total_after = [], 0.0, 0.0
    for repo in repos:
        per_day = usage.get(repo, {})
        before = sum(per_day.get(d, 0) for d in billing["before_days"]) / BILLING_WINDOW_DAYS
        after = sum(per_day.get(d, 0) for d in billing["after_days"]) / BILLING_WINDOW_DAYS
        total_before += before
        total_after += after
        rows.append(f"| {repo} | {before:.2f} | {after:.2f} | {before - after:+.2f} |")
    saved_usd = (total_before - total_after) * 30
    model = billing.get("model_usd_month", 400)
    verdict = "FAKT" if saved_usd >= 0.8 * model else "DATA_GAP"
    sku_rows = "\n".join(f"| {sku} | {v['before'] / BILLING_WINDOW_DAYS:.0f} | {v['after'] / BILLING_WINDOW_DAYS:.0f} |"
                         for sku, v in sorted(skus.items()))
    body = (f"### Akceptace #{BILLING_SUB} — účtované USD/den před a po\n\n"
            f"Před: {billing['before_days'][0]} až {billing['before_days'][-1]}; "
            f"po: {billing['after_days'][0]} až {billing['after_days'][-1]} (billing usage API, gross, Actions bez úložiště).\n\n"
            "| repo | USD/den před | USD/den po | rozdíl |\n|---|---|---|---|\n" + "\n".join(rows)
            + f"\n| **celkem** | **{total_before:.2f}** | **{total_after:.2f}** | **{total_before - total_after:+.2f}** |\n\n"
            "| SKU | min/den před | min/den po |\n|---|---|---|\n" + sku_rows + "\n\n"
            f"Úspora ≈ **{saved_usd:.0f} USD/měsíc** proti modelu {model} USD/měsíc (cíl ≥ 80 %). "
            f"Verdikt: **{verdict}**.\n\n"
            "Souběh: okno „před“ zahrnuje část efektu EPIC #888 (poslední merge 23. 9.); billing API je po "
            "repech, ne po workflow. Surová data: `~/.merglbot/gh-cost-910/billing/acceptance.json`.")
    if not comment(EPIC_REPO, BILLING_SUB, body, state, "billing:posted"):
        return False
    billing.update(posted_at=iso(), saved_usd_month=round(saved_usd, 1), verdict=verdict)
    save_state(state)
    if verdict == "FAKT":
        if not DRY_RUN:
            gh("issue", "close", str(BILLING_SUB), "-R", EPIC_REPO, "--reason", "completed")
        board(BILLING_SUB, STATUS_DONE)
        state["subs"].setdefault(str(BILLING_SUB), {})["board_done_at"] = iso()
        save_state(state)
    notify("EPIC 910", f"Billing akceptace: {saved_usd:.0f} USD/měs., {verdict}")
    return True
