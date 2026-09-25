"""Exercise the patched production closeout function against actual helper contracts."""

import importlib.util
import datetime as dt
import pathlib
import unittest

HERE = pathlib.Path(__file__).parent
spec = importlib.util.spec_from_file_location("cost_888_patch", HERE / "patch_autopilot.py")
patcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(patcher)


class CloseoutLifecycle(unittest.TestCase):
    def setUp(self):
        source = patcher.patch((HERE / "fixture_legacy.py").read_text())
        self.calls = []
        self.issue_state = "closed"
        self.reopen_ok = True
        self.close_ok = True
        self.board_ok = True
        self.exception_ok = True
        self.stamps = {"dod:892"}  # The historical proxy already posted a comment.

        def gh(*args):
            self.calls.append(("gh", args))
            if args[:2] == ("issue", "reopen"):
                if not self.reopen_ok:
                    return 1, "", "reopen failed"
                self.issue_state = "open"
            if args[:2] == ("issue", "close"):
                if not self.close_ok:
                    return 1, "", "close failed"
                self.issue_state = "closed"
            return 0, "", ""

        def gh_json(path):
            self.calls.append(("api", path))
            if path.endswith(("/issues/892", "/issues/889")):
                return {"state": self.issue_state}
            if path.endswith("/pulls/184"):
                return {"state": "closed", "merged": False} if self.exception_ok else {"state": "open"}
            if path.endswith("/branches/main/protection"):
                return {"required_status_checks": {"checks": [
                    {"context": c} for c in ("gitleaks", "dependency-review", "Merglbot PR Assistant v6")]}}
            raise AssertionError(path)

        def board(number, option):
            self.calls.append(("board", number, option))
            return self.board_ok

        def comment(repo, number, body, state, key):
            self.calls.append(("comment", number, key, body))
            if key in self.stamps:
                return False
            self.stamps.add(key)
            return True

        self.scope = {"gh": gh, "gh_json": gh_json, "board": board,
                      "comment": comment, "save_state": lambda state: self.calls.append(("save",)),
                      "stamped": lambda state, key: key in self.stamps,
                      "log": lambda msg: None, "iso": lambda: "2026-09-25T13:00:00Z",
                      "dod_row": lambda item: "| row | evidence |",
                      "DRY_RUN": False, "EPIC_REPO": "merglbot-core/github",
                      "STATUS_IN_PROGRESS": "in-progress", "STATUS_DONE": "done",
                      "DOD_MAX_SECONDS": 60, "DOD_RUNS": 5, "DOD_RUNS_LOW_TRAFFIC": 2,
                      "BILLING_SUB": 896}
        exec(compile(source, str(HERE / "fixture_legacy.py"), "exec"), self.scope)

    def state_892(self):
        return {"subs": {"892": {"board_done_at": "historical"}},
                "dod": {"892|merglbot-core/merglbot-admin": {"sub": 892,
                         "repo": "merglbot-core/merglbot-admin", "met_at": "historical_proxy",
                         "literal_verified": False}}}

    def test_reopen_then_close_with_new_comment_key(self):
        state = self.state_892()
        close = self.scope["close_finished_subs"]
        self.assertTrue(close(state))
        self.assertIsNone(state["subs"]["892"]["board_done_at"])
        self.assertEqual(self.issue_state, "open")
        self.assertEqual([c[:3] for c in self.calls if c[0] == "board"],
                         [("board", 892, "in-progress")])
        self.assertFalse(close(state))  # Stale met_at alone cannot close it.
        state["dod"]["892|merglbot-core/merglbot-admin"].update(
            literal_verified=True, met_at="2026-09-25T13:00:00Z")
        self.assertTrue(close(state))
        self.assertEqual(self.issue_state, "closed")
        self.assertIn("dod:892:literal-v2", self.stamps)
        self.assertEqual(state["subs"]["892"]["board_done_at"], "2026-09-25T13:00:00Z")
        self.assertEqual(state["subs"]["892"]["literal_closeout_at"], "2026-09-25T13:00:00Z")
        self.scope.update(dt=dt, RUN_COUNT_INTERVAL_HOURS=6, CALLS={"n": 0},
                          MAX_CALLS_PER_TICK=60,
                          parse=lambda value: dt.datetime.fromisoformat(value.replace("Z", "+00:00")),
                          now=lambda: dt.datetime(2026, 9, 26, tzinfo=dt.timezone.utc),
                          phase=lambda *args: self.calls.append(("phase", args)))
        self.scope["MEASURES"]["filtered"] = lambda *_: True
        state["dod"]["892|merglbot-core/merglbot-admin"]["kind"] = "filtered"
        self.assertTrue(self.scope["measure_dod"](state))
        self.assertFalse(any(call[0] == "phase" for call in self.calls))
        state["dod"]["892|merglbot-core/merglbot-admin"]["literal_verified"] = False
        self.assertFalse(close(state))  # A completed literal closeout is never reopened by proxy logic.

    def test_failed_reopen_or_board_does_not_record_completion(self):
        state = self.state_892()
        self.reopen_ok = False
        self.assertFalse(self.scope["close_finished_subs"](state))
        self.assertEqual(state["subs"]["892"]["board_done_at"], "historical")
        self.reopen_ok = True
        self.board_ok = False
        self.assertFalse(self.scope["close_finished_subs"](state))
        self.assertEqual(state["subs"]["892"]["board_done_at"], "historical")
        self.board_ok = True
        self.assertTrue(self.scope["close_finished_subs"](state))  # Already-open retry.

    def test_dry_run_does_not_consume_pending_reopen(self):
        state = self.state_892()
        self.scope["DRY_RUN"] = True
        self.assertFalse(self.scope["close_finished_subs"](state))
        self.assertEqual(state["subs"]["892"]["board_done_at"], "historical")
        self.assertEqual(self.issue_state, "closed")
        self.assertFalse(any(c[0] in ("api", "gh", "board", "save") for c in self.calls))
        self.scope["DRY_RUN"] = False
        self.assertTrue(self.scope["close_finished_subs"](state))
        self.assertEqual(self.issue_state, "open")

    def test_failed_close_and_board_retry_without_duplicate_comment(self):
        state = self.state_892()
        close = self.scope["close_finished_subs"]
        self.assertTrue(close(state))  # Reopen stale proxy.
        state["dod"]["892|merglbot-core/merglbot-admin"].update(
            literal_verified=True, met_at="2026-09-25T13:00:00Z")
        self.close_ok = False
        self.assertFalse(close(state))
        self.assertIsNone(state["subs"]["892"]["board_done_at"])
        self.assertIn("dod:892:literal-v2", self.stamps)
        self.close_ok = True
        self.board_ok = False
        self.assertFalse(close(state))
        self.assertEqual(self.issue_state, "closed")
        self.assertIsNone(state["subs"]["892"]["board_done_at"])
        self.board_ok = True
        self.assertTrue(close(state))
        self.assertEqual(state["subs"]["892"]["board_done_at"], "2026-09-25T13:00:00Z")
        comments = [c for c in self.calls if c[0] == "comment" and c[2] == "dod:892:literal-v2"]
        self.assertEqual(len(comments), 1)

    def test_889_exception_accepts_only_preserved_protection(self):
        excluded = {"sub": 889, "repo": "merglbot-proteinaco/acquisition-analysis", "met_at": None}
        state = {"subs": {"889": {"board_done_at": None}},
                 "dod": {"889|merglbot-proteinaco/acquisition-analysis": excluded,
                         "889|other/repo": {"sub": 889, "repo": "other/repo", "met_at": "ok"}}}
        self.exception_ok = False
        self.assertFalse(self.scope["close_finished_subs"](state))
        self.assertIsNone(state["subs"]["889"]["board_done_at"])
        self.exception_ok = True
        self.assertTrue(self.scope["close_finished_subs"](state))
        self.assertIsNone(excluded["met_at"])  # Exclusion is never counted as implementation.
        self.assertIn("Ekonomická výjimka", [c[3] for c in self.calls if c[0] == "comment"][-1])

    def test_rejected_889_exception_does_not_block_892_reopen(self):
        state = self.state_892()
        state["subs"] = {"889": {"board_done_at": None}, **state["subs"]}
        state["dod"]["889|merglbot-proteinaco/acquisition-analysis"] = {
            "sub": 889, "repo": "merglbot-proteinaco/acquisition-analysis", "met_at": None}
        state["dod"]["889|other/repo"] = {"sub": 889, "repo": "other/repo", "met_at": "ok"}
        self.exception_ok = False
        self.assertTrue(self.scope["close_finished_subs"](state))
        self.assertEqual(self.issue_state, "open")


if __name__ == "__main__":
    unittest.main()
