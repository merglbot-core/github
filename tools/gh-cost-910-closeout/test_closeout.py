import base64
import importlib.util
import pathlib
import unittest

HERE = pathlib.Path(__file__).parent
spec = importlib.util.spec_from_file_location("closeout_patch", HERE / "patch_autopilot.py")
patcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(patcher)


class Closeout(unittest.TestCase):
    def setUp(self):
        source = patcher.patch((HERE / "fixture_legacy.py").read_text())
        self.calls = []
        self.stamps = set()
        self.issues = {"913": "open", "914": "open", "921": "open", "910": "open",
                       "930": "closed"}
        self.close_ok = True
        self.board_ok = True
        self.exception_pr_closed = True
        self.weekly_schedule = True
        self.schedule_expression = "30 2 * * 1"
        self.project_done = True
        self.children_complete = True
        self.main_sha = "head"

        def gh_json(path):
            self.calls.append(("api", path))
            if path.endswith("/pulls/35"):
                return {"state": "closed" if self.exception_pr_closed else "open", "merged": False}
            if "/sub_issues?" in path:
                if not self.children_complete:
                    return None
                return [{"number": int(k), "state": v}
                        for k, v in self.issues.items() if k != "910"]
            if path.endswith("/branches/main"):
                return {"commit": {"sha": self.main_sha}}
            if path.endswith("/codeql-analysis.yml?ref=head"):
                source = ("on:\n  push:\n    branches: [main, release/*]\n"
                          "  schedule:\n    - cron: '" + self.schedule_expression + "'\n"
                          ) if self.weekly_schedule else "on:\n  push:\n"
                return {"content": base64.b64encode(source.encode()).decode()}
            if "/issues/" in path:
                return {"state": self.issues[path.rsplit("/", 1)[1]]}
            raise AssertionError(path)

        def gh(*args):
            self.calls.append(("gh", args))
            if args[:2] == ("issue", "close"):
                if not self.close_ok:
                    return 1, "", "failure"
                self.issues[str(args[2])] = "closed"
            return 0, "", ""

        def comment(repo, number, body, state, key):
            self.calls.append(("comment", number, key, body))
            if key in self.stamps:
                return False
            self.stamps.add(key)
            return True

        def board(number, option):
            self.calls.append(("board", number, option))
            return self.board_ok

        item_map = {int(k): "item-" + k for k in self.issues}
        def gh_graphql(query):
            self.calls.append(("graphql", query))
            for item in item_map.values():
                if 'node(id:"' + item + '")' in query:
                    return {"node": {"id": item, "project": {"id": "project-66"},
                                     "fieldValueByName": {"optionId": "done" if self.project_done else "todo"}}}
            raise AssertionError(query)

        scope = {"gh_json": gh_json, "gh": gh, "comment": comment, "board": board,
                 "gh_graphql": gh_graphql, "BOARD_ITEMS": item_map, "PROJECT_ID": "project-66",
                 "stamped": lambda state, key: key in self.stamps,
                 "save_state": lambda state: self.calls.append(("save",)),
                 "log": lambda message: None, "notify": lambda *args: None,
                 "iso": lambda: "2026-09-25T13:00:00Z", "dod_row": lambda item: "| row | proof |",
                 "DRY_RUN": False, "EPIC_REPO": "merglbot-core/github", "EPIC": 910,
                 "BILLING_SUB": 922, "STATUS_DONE": "done", "DOD_MAX_SECONDS": 60,
                 "LOW_TRAFFIC_GRACE_DAYS": 14}
        exec(compile(source, str(HERE / "fixture_legacy.py"), "exec"), scope)
        self.scope = scope

    def test_921_hold_prevents_closeout_even_with_historical_met_marks(self):
        state = {"subs": {"921": {"board_done_at": None, "technical_hold": True}},
                 "dod": {"921|repo": {"sub": 921, "repo": "repo", "kind": "file_state", "met_at": "old"}}}
        self.assertFalse(self.scope["close_finished_subs"](state))
        self.assertFalse(any(c[0] == "comment" for c in self.calls))

    def test_close_retry_confirms_issue_and_board_before_state(self):
        state = {"subs": {"913": {}}, "dod": {"913|repo": {"sub": 913, "repo": "repo", "kind": "file_state", "met_at": "ok"}}}
        close = self.scope["close_finished_subs"]
        self.close_ok = False
        self.assertFalse(close(state))
        self.assertNotIn("board_done_at", state["subs"]["913"])
        self.close_ok = True
        self.board_ok = False
        self.assertFalse(close(state))
        self.assertEqual(self.issues["913"], "closed")
        self.assertNotIn("board_done_at", state["subs"]["913"])
        self.board_ok = True
        self.assertTrue(close(state))
        self.assertEqual(state["subs"]["913"]["board_done_at"], "2026-09-25T13:00:00Z")
        self.assertEqual(len([c for c in self.calls if c[0] == "comment"]), 1)

    def test_914_exception_requires_closed_unmerged_pr_and_weekly_signal(self):
        state = {"subs": {"914": {}}, "dod": {
            "914|repo": {"sub": 914, "repo": "repo", "kind": "file_state", "met_at": "ok"},
            "914|merglbot-milan-private/plane_so": {
                "sub": 914, "repo": "merglbot-milan-private/plane_so",
                "kind": "no_push_runs", "met_at": None}}}
        state["dod"]["914|repo"]["met_at"] = None
        self.assertFalse(self.scope["close_finished_subs"](state))
        state["dod"]["914|repo"]["met_at"] = "ok"
        self.exception_pr_closed = False
        self.assertFalse(self.scope["close_finished_subs"](state))
        self.exception_pr_closed = True
        self.weekly_schedule = False
        self.assertFalse(self.scope["close_finished_subs"](state))
        self.weekly_schedule = True
        self.schedule_expression = "30 2 * * 2"
        self.assertFalse(self.scope["close_finished_subs"](state))
        self.schedule_expression = "30 2 * * 1"
        self.assertTrue(self.scope["close_finished_subs"](state))
        body = [c[3] for c in self.calls if c[0] == "comment"][-1]
        self.assertIn("Ekonomická výjimka", body)
        self.assertIn("není implementovaná úspora", body)
        self.assertEqual(body.count("| row | proof |"), 1)

    def test_epic_requires_live_closed_subissues_and_no_hold(self):
        state = {"subs": {"921": {"board_done_at": "old", "technical_hold": True}},
                 "billing": {"saved_usd_month": 400, "verdict": "FAKT"}}
        self.assertFalse(self.scope["close_epic"](state))
        state["subs"]["921"].pop("technical_hold")
        self.assertFalse(self.scope["close_epic"](state))
        self.issues["921"] = "closed"
        self.issues["913"] = "closed"
        self.issues["914"] = "closed"
        self.board_ok = False
        self.assertFalse(self.scope["close_epic"](state))
        self.assertNotIn("closed_at", state)
        self.board_ok = True
        self.assertTrue(self.scope["close_epic"](state))
        self.assertEqual(state["closed_at"], "2026-09-25T13:00:00Z")

    def test_historical_markers_cannot_close_when_project_or_subissue_missing(self):
        state = {"subs": {"921": {"board_done_at": "old"}}, "billing": {}}
        self.issues.update({"921": "closed", "913": "closed", "914": "closed"})
        self.project_done = False
        self.assertFalse(self.scope["close_epic"](state))
        self.project_done = True
        self.children_complete = False
        self.assertFalse(self.scope["close_epic"](state))
        self.assertNotIn("closed_at", state)


if __name__ == "__main__":
    unittest.main()
