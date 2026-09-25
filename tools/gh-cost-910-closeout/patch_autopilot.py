"""Fail-closed issue/Project lifecycle for the local #910 cost autopilot."""


NEW_CLOSE_SUBS = '''def close_finished_subs(state):
    for sub, record in state.get("subs", {}).items():
        # #921 was reopened after a full current-main census found 39 jobs still
        # without limits. Historical file-state marks cannot discharge that DoD.
        if str(sub) == "921" and record.get("technical_hold"):
            continue
        if record.get("board_done_at") or record.get("closed_elsewhere"):
            continue
        items = [i for i in state.get("dod", {}).values() if str(i.get("sub")) == str(sub)]
        if not items or any(not i.get("met_at") for i in items):
            continue
        exception = ""
        if str(sub) == "914":
            # One originally proposed repo is a documented economic exception,
            # never an implemented saving. Its weekly CodeQL signal must remain.
            pr = gh_json("repos/merglbot-milan-private/plane_so/pulls/35")
            branch = gh_json("repos/merglbot-milan-private/plane_so/branches/main")
            main_sha = (branch or {}).get("commit", {}).get("sha")
            if pr is None or pr.get("state") != "closed" or pr.get("merged") or not main_sha:
                continue
            wf = gh_json(f"repos/merglbot-milan-private/plane_so/contents/.github/workflows/codeql-analysis.yml?ref={main_sha}")
            if not wf or not wf.get("content"):
                continue
            import base64
            import re
            source = base64.b64decode(wf["content"]).decode("utf-8", "replace")
            # This low-traffic repository is a documented exception to the
            # no-push saving. Preserve its existing push coverage and require
            # the literal weekly Monday CodeQL schedule, not any cron.
            if not (re.search(r"(?m)^on:\\s*$", source)
                    and re.search(r"(?m)^  push:\\s*$", source)
                    and re.search(r"(?m)^    branches: \\[main, release/\\*\\]\\s*$", source)
                    and re.search(r"(?m)^  schedule:\\s*$", source)
                    and re.search(r"(?m)^    - cron: ['\\\"]30 2 \\* \\* 1['\\\"]\\s*$", source)):
                continue
            current = gh_json("repos/merglbot-milan-private/plane_so/branches/main")
            if (current or {}).get("commit", {}).get("sha") != main_sha:
                continue
            exception = ("\\n\\nEkonomická výjimka: plane_so PR #35 je zavřen bez merge; "
                         "týdenní CodeQL signál zůstává. Tento repozitář není implementovaná úspora "
                         "a je vyřazen z modelu. PMA a acquisition-analysis zůstávají mimo tuto migraci.")
        body = ("### DoD změřeno na provozu\\n\\n| repo | měření |\\n|---|---|\\n"
                + "\\n".join(dod_row(i) for i in items)
                + "\\n\\nKritérium podle druhu: odklad = ≥ 5 first-attempt PR jobů čekalo ≥ 14 min a žádný "
                  "nestartoval dřív; runner = ≥ 5 zelených běhů na novém labelu pod limitem; bez push = 0 "
                  "push běhů workflow po merge (u CodeQL ≥ 1 týdenní běh); soubor = změna je na main. "
                  f"Účtovaná část DoD se uzavírá v akceptaci #{BILLING_SUB}."
                + exception + "\\n\\nZavírám a přepínám na Done.")
        if DRY_RUN:
            continue
        key = f"dod:{sub}"
        if not (stamped(state, key) or comment(EPIC_REPO, int(sub), body, state, key)):
            continue
        issue = gh_json(f"repos/{EPIC_REPO}/issues/{sub}")
        if issue is None or issue.get("state") not in ("open", "closed"):
            return False
        if issue["state"] == "open":
            code, _, _ = gh("issue", "close", str(sub), "-R", EPIC_REPO, "--reason", "completed")
            if code != 0:
                return False
        if not board(int(sub), STATUS_DONE) or not project_status_done(int(sub)):
            return False
        record["board_done_at"] = iso()
        save_state(state)
        log(f"sub-issue #{sub} closed, board Done")
        return True
    return False
'''


NEW_PROJECT_STATUS = '''def project_status_done(issue_number):
    """Confirm the Project 66 item belongs to this board and is Done."""
    item_id = BOARD_ITEMS.get(int(issue_number))
    if not item_id:
        return False
    query = ('query { node(id:"' + item_id + '") { ... on ProjectV2Item '
             '{ id project { id } fieldValueByName(name:"Status") '
             '{ ... on ProjectV2ItemFieldSingleSelectValue { optionId } } } } }')
    data = gh_graphql(query)
    node = (data or {}).get("node") or {}
    return (node.get("id") == item_id
            and (node.get("project") or {}).get("id") == PROJECT_ID
            and (node.get("fieldValueByName") or {}).get("optionId") == STATUS_DONE)
'''


NEW_CLOSE_EPIC = '''def close_epic(state):
    """Close only after state, live sub-issues and Project transitions agree."""
    if state.get("closed_at"):
        return False
    if any(rec.get("technical_hold") for rec in state.get("subs", {}).values()):
        return False
    open_subs = [sub for sub, rec in state.get("subs", {}).items()
                 if not rec.get("board_done_at") and not rec.get("closed_elsewhere")]
    if open_subs:
        return False
    # Read the canonical sub-issue collection, including children omitted from
    # the local registry such as #930. A full page requires another page.
    seen = set()
    for page in range(1, 11):
        children = gh_json(f"repos/{EPIC_REPO}/issues/{EPIC}/sub_issues?per_page=100&page={page}")
        if not isinstance(children, list):
            return False
        for child in children:
            if not isinstance(child, dict) or not isinstance(child.get("number"), int):
                return False
            if child["number"] in seen or child.get("state") != "closed":
                return False
            seen.add(child["number"])
        if len(children) < 100:
            break
    else:
        return False
    if not seen or not set(map(int, state.get("subs", {}))).issubset(seen):
        return False
    for sub in seen:
        if not project_status_done(sub):
            return False
    body = ("### EPIC uzavřen\\n\\nSub-issues jsou živě zavřené a Project potvrzený; "
            f"akceptace #{BILLING_SUB} je zapsaná ({state['billing'].get('saved_usd_month')} USD/měsíc, "
            f"{state['billing'].get('verdict')}). Jde o účtovaný gross model; finální netto "
            "akceptace v #934 je samostatná. Autopilot tímto končí; launchd job "
            "`ai.merglbot.gh-cost-910-autopilot` už nic nedělá a může se odinstalovat.")
    if not (stamped(state, "epic:closed") or comment(EPIC_REPO, EPIC, body, state, "epic:closed")):
        return False
    if not DRY_RUN:
        issue = gh_json(f"repos/{EPIC_REPO}/issues/{EPIC}")
        if issue is None or issue.get("state") not in ("open", "closed"):
            return False
        if issue["state"] == "open":
            code, _, _ = gh("issue", "close", str(EPIC), "-R", EPIC_REPO, "--reason", "completed")
            if code != 0:
                return False
        if not board(EPIC, STATUS_DONE) or not project_status_done(EPIC):
            return False
    state["closed_at"] = iso()
    save_state(state)
    notify("EPIC 910", "EPIC uzavřen")
    log("EPIC closed")
    return True
'''


def patch(source):
    a = source.index("def close_finished_subs(state):\n")
    b = source.index("\n\n# --------------------------------------------------------------------------- billing", a)
    old = source[a:b]
    if 'record["board_done_at"] = iso()' not in old or 'f"dod:{sub}"' not in old:
        raise ValueError("close_finished_subs drift")
    source = source[:a] + NEW_PROJECT_STATUS + "\n\n" + NEW_CLOSE_SUBS + source[b:]
    a = source.index("def close_epic(state):\n")
    b = source.index("\n\n# --------------------------------------------------------------------------- daily note", a)
    old = source[a:b]
    if 'state["closed_at"] = iso()' not in old or 'open_subs' not in old:
        raise ValueError("close_epic drift")
    return source[:a] + NEW_CLOSE_EPIC + source[b:]
