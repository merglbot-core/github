"""Injected billing-window guard; uses the existing autopilot's I/O helpers."""

BILLING_REQUIRED_FOLLOWUPS = (
    "merglbot-extractors/denatura-forecast-exporter#467",
    "merglbot-denatura/acquisition-analysis#199",
    "merglbot-proteinaco/acquisition-analysis#186",
    "merglbot-core/merglbot-admin#1104",
    "merglbot-core/project-management-app#395",
    "Merglevsky-cz/Merglbot.io#53",
    "Merglevsky-cz/shoptet_bq_all_clients#32",
    "merglbot-core/infra#3154",
)


def billing_inputs(state):
    """Keep financial follow-ups separate from rollout verification/DoD records."""
    import re
    if state.get("registration_complete") is not True:
        raise ValueError("registration incomplete")
    records = state.get("prs")
    followups = state.get("billing_followups")
    if not isinstance(records, dict) or not records or not isinstance(followups, dict):
        raise ValueError("billing registry missing")
    if not set(BILLING_REQUIRED_FOLLOWUPS).issubset(followups):
        raise ValueError("known follow-up registry incomplete")
    merged = []
    for source, entries in (("rollout", records), ("followup", followups)):
        for key, record in entries.items():
            if not isinstance(key, str) or not re.fullmatch(r"[\w.-]+/[\w.-]+#[1-9][0-9]*", key):
                raise ValueError("invalid PR key")
            if not isinstance(record, dict):
                raise ValueError("invalid PR record")
            if source == "rollout":
                if record.get("state") not in ("verified", "failed", "dropped"):
                    raise ValueError("rollout pending or unknown")
                stamp = record.get("merged_at")
                if not stamp and record.get("state") in ("failed", "dropped"):
                    continue
            else:
                if key in records:
                    raise ValueError("duplicate financial follow-up")
                if record.get("scope_issue") != "merglbot-core/github#921":
                    raise ValueError("follow-up scope not verified")
                if record.get("base_ref") != "main" or not re.fullmatch(
                        r"[0-9a-f]{40}", record.get("merge_sha", "")):
                    raise ValueError("follow-up merge not verified")
                stamp = record.get("merged_at")
            if not isinstance(stamp, str) or not stamp:
                raise ValueError("merge timestamp missing")
            if not isinstance(record.get("merge_sha"), str) or not re.fullmatch(
                    r"[0-9a-f]{40}", record["merge_sha"]):
                raise ValueError("merge SHA missing or malformed")
            instant = dt.datetime.fromisoformat(stamp.replace("Z", "+00:00"))
            if instant.tzinfo is None or instant.utcoffset() is None:
                raise ValueError("merge timestamp lacks timezone")
            instant = instant.astimezone(dt.timezone.utc)
            if instant > now():
                raise ValueError("merge timestamp is in the future")
            merged.append((instant, key))
    if not merged:
        raise ValueError("no merged billing input")
    return sorted(merged)


def billing_window(state):
    inputs = billing_inputs(state)
    first, last = inputs[0][0], inputs[-1][0]
    first_day, last_day = first.date(), last.date()
    before = billing_days(dt.datetime.combine(first_day, dt.time())
                          - dt.timedelta(days=BILLING_WINDOW_DAYS), BILLING_WINDOW_DAYS)
    after = billing_days(dt.datetime.combine(last_day, dt.time())
                         + dt.timedelta(days=1), BILLING_WINDOW_DAYS)
    due = dt.datetime.combine(last_day, dt.time(6), tzinfo=dt.timezone.utc) \
        + dt.timedelta(days=BILLING_WINDOW_DAYS + 2)
    # Include every merge, not only extrema: adding an input inside the old
    # window still changes the measured scope and invalidates old acceptance.
    import hashlib
    import json
    import re
    extra = state.get("billing", {}).get("extra_repos", [])
    if not isinstance(extra, list) or any(not isinstance(repo, str) or not re.fullmatch(
            r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo) for repo in extra):
        raise ValueError("invalid extra billing repository scope")
    records = {**state["prs"], **state["billing_followups"]}
    identity = {"merges": [[iso(instant), key, records[key]["merge_sha"]] for instant, key in inputs],
                "repos": sorted({key.rsplit("#", 1)[0] for _, key in inputs} | set(extra))}
    fingerprint = hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return dict(first_merge_at=iso(first), last_merge_at=iso(last),
                before_days=before, after_days=after, due_at=iso(due),
                input_fingerprint=fingerprint)


def billing_acceptance(state):
    """Revalidate the current input set before scheduling, posting or closing."""
    billing = state.setdefault("billing", {})
    try:
        window = billing_window(state)
    except (ValueError, TypeError, OverflowError) as error:
        log(f"billing blocked: {error}")
        return False
    changed = any(billing.get(key) != value for key, value in window.items())
    if changed:
        if billing.get("posted_at"):
            # Preserve historical proof, but do not close/reaccept a changed
            # scope automatically. An already-closed issue needs reconciliation.
            if not billing.get("scope_drift"):
                billing["scope_drift"] = {"observed_at": iso(), "proposed_window": window}
                save_state(state)
                log("billing blocked: published acceptance has a changed scope")
            return False
        billing.update(window)
        save_state(state)
    if billing.get("scope_drift"):
        return False
    if billing.get("posted_at"):
        return close_epic(state)
    key = "billing:scheduled:" + window["input_fingerprint"]
    if not stamped(state, key):
        due = parse(window["due_at"])
        before, after = window["before_days"], window["after_days"]
        body = (f"### Akceptační okno aktualizováno\n\nPoslední relevantní merge: "
                f"{prague(window['last_merge_at'])}. Billingová UTC denní okna: "
                f"před {before[0]} až {before[-1]}, po {after[0]} až {after[-1]}. "
                f"Nejdřívější kontrola autopilotu: {prague(due)}. "
                "Další relevantní merge může okno posunout. Jde o plán, nikoli finanční akceptaci.")
        if not comment(EPIC_REPO, BILLING_SUB, body, state, key):
            return False
        return True
    if now() < parse(window["due_at"]):
        return False
    return post_billing(state, billing)
