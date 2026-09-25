def close_finished_subs(state):
    for sub, record in state.get("subs", {}).items():
        if record.get("board_done_at") or record.get("closed_elsewhere"):
            continue
        items = [i for i in state.get("dod", {}).values() if str(i.get("sub")) == str(sub)]
        if not items or any(not i.get("met_at") for i in items):
            continue
        body = ("### DoD změřeno na provozu\n\n| repo | měření |\n|---|---|\n"
                + "\n".join(dod_row(i) for i in items)
                + "\n\nKritérium podle druhu: odklad = ≥ 5 first-attempt PR jobů čekalo ≥ 14 min a žádný "
                  "nestartoval dřív; runner = ≥ 5 zelených běhů na novém labelu pod limitem; bez push = 0 "
                  "push běhů workflow po merge (u CodeQL ≥ 1 týdenní běh); soubor = změna je na main. "
                  f"Účtovaná část DoD se uzavírá v akceptaci #{BILLING_SUB}.\n\nZavírám a přepínám na Done.")
        if comment(EPIC_REPO, int(sub), body, state, f"dod:{sub}"):
            if not DRY_RUN:
                gh("issue", "close", str(sub), "-R", EPIC_REPO, "--reason", "completed")
            board(int(sub), STATUS_DONE)
            record["board_done_at"] = iso()
            save_state(state)
            log(f"sub-issue #{sub} closed, board Done")
            return True
    return False


# --------------------------------------------------------------------------- billing

def close_epic(state):
    """Close the EPIC once every sub-issue is Done; afterwards every tick exits immediately."""
    if state.get("closed_at"):
        return False
    open_subs = [sub for sub, rec in state.get("subs", {}).items()
                 if not rec.get("board_done_at") and not rec.get("closed_elsewhere")]
    if open_subs:
        return False
    body = ("### EPIC uzavřen\n\nVšechny sub-issues mají DoD změřenou na provozu a akceptace "
            f"#{BILLING_SUB} je zapsaná ({state['billing'].get('saved_usd_month')} USD/měsíc, "
            f"{state['billing'].get('verdict')}). Autopilot tímto končí; launchd job "
            "`ai.merglbot.gh-cost-910-autopilot` už nic nedělá a může se odinstalovat.")
    if not comment(EPIC_REPO, EPIC, body, state, "epic:closed"):
        return False
    if not DRY_RUN:
        gh("issue", "close", str(EPIC), "-R", EPIC_REPO, "--reason", "completed")
    board(EPIC, STATUS_DONE)
    state["closed_at"] = iso()
    save_state(state)
    notify("EPIC 910", "EPIC uzavřen")
    log("EPIC closed")
    return True


# --------------------------------------------------------------------------- daily note
