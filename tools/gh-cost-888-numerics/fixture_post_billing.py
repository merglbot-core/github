"""Actual #888 numeric/control flow; report prose shortened; offline I/O only."""

def post_billing(state, billing):
    repos = sorted({key.rsplit("#", 1)[0] for key, pr in state["prs"].items() if pr.get("merged_at")})
    orgs = sorted({repo.split("/")[0] for repo in repos})
    days = billing["before_days"] + billing["after_days"]
    months = sorted({(int(d[:4]), int(d[5:7])) for d in days})
    usage = {}
    for org in orgs:
        for year, month in months:
            data = gh_json(f"organizations/{org}/settings/billing/usage?year={year}&month={month}")
            if data is None:
                log(f"billing usage for {org} {year}-{month} unavailable, retrying next tick")
                return False
            for item in data.get("usageItems", []):
                if (item.get("product") or "").lower() != "actions" or "linux" not in (item.get("sku") or "").lower():
                    continue
                repo = f"{item.get('organizationName')}/{item.get('repositoryName')}"
                day = (item.get("date") or "")[:10]
                usage.setdefault(repo, {}).setdefault(day, 0.0)
                usage[repo][day] += item.get("quantity") or 0
    BILLING_DIR.mkdir(parents=True, exist_ok=True)
    (BILLING_DIR / "acceptance.json").write_text(json.dumps(usage, indent=1))
    rows, total_before, total_after = [], 0.0, 0.0
    for repo in repos:
        per_day = usage.get(repo, {})
        before = sum(per_day.get(d, 0) for d in billing["before_days"]) / BILLING_WINDOW_DAYS
        after = sum(per_day.get(d, 0) for d in billing["after_days"]) / BILLING_WINDOW_DAYS
        total_before += before
        total_after += after
        rows.append(f"| {repo} | {before:.0f} | {after:.0f} | {before - after:+.0f} |")
    saved_usd = (total_before - total_after) * 30 * ACTIONS_USD_PER_MINUTE
    model = billing.get("model_usd_month", 150)
    verdict = "FAKT" if saved_usd >= 0.8 * model else "DATA_GAP"
    body = "Offline #896 report fixture"
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
    notify("EPIC 888", f"Billing akceptace: {saved_usd:.0f} USD/měs., {verdict}")
    return True
