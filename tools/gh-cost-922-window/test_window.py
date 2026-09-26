import copy
import datetime as dt
import pathlib
import unittest

HERE = pathlib.Path(__file__).parent
UTC = dt.timezone.utc


class WindowTests(unittest.TestCase):
    def setUp(self):
        self.calls, self.stamps = [], set()
        self.clock = dt.datetime(2026, 9, 27, tzinfo=UTC)
        def iso(value=None):
            return (value or self.clock).isoformat().replace("+00:00", "Z")
        def comment(*args):
            self.calls.append("comment")
            self.stamps.add(args[-1])
            return True
        self.scope = dict(dt=dt, BILLING_WINDOW_DAYS=14, EPIC_REPO="merglbot-core/github",
                          BILLING_SUB=922, now=lambda: self.clock, iso=iso,
                          parse=lambda value: dt.datetime.fromisoformat(value.replace("Z", "+00:00")),
                          prague=lambda value: str(value), log=lambda value: self.calls.append("log"),
                          save_state=lambda value: self.calls.append("save"), comment=comment,
                          stamped=lambda state, key: key in self.stamps,
                          close_epic=lambda state: self.calls.append("close") or True,
                          post_billing=lambda state, billing: self.calls.append("post") or True,
                          billing_days=lambda start, days: [(start + dt.timedelta(days=i)).strftime("%Y-%m-%d") for i in range(days)])
        exec(compile((HERE / "runtime.py").read_text(), "runtime.py", "exec"), self.scope)
        self.state = dict(registration_complete=True, prs={
            "org/repo#1": dict(state="verified", merged_at="2026-09-23T19:34:46Z"),
            "org/repo#2": dict(state="verified", merged_at="2026-09-24T17:29:53Z")})
        # Synthetic metadata models an explicitly complete migrated registry.
        self.state["billing_followups"] = {key: dict(merged_at="2026-09-24T12:00:00Z",
            merge_sha="a" * 40, base_ref="main", scope_issue="merglbot-core/github#921")
            for key in self.scope["BILLING_REQUIRED_FOLLOWUPS"]}

    def run_handler(self):
        return self.scope["billing_acceptance"](self.state)

    def add_followup(self, stamp="2026-09-26T17:43:21Z", key="other/repo#3"):
        self.state.setdefault("billing_followups", {})[key] = dict(
            merged_at=stamp, merge_sha="a" * 40, base_ref="main",
            scope_issue="merglbot-core/github#921")

    def test_late_followup_moves_cached_window(self):
        self.run_handler()
        self.assertEqual(self.state["billing"]["due_at"], "2026-10-10T06:00:00Z")
        self.add_followup()
        self.run_handler()
        billing = self.state["billing"]
        self.assertEqual(billing["due_at"], "2026-10-12T06:00:00Z")
        self.assertEqual(billing["after_days"][0], "2026-09-27")
        self.assertEqual(billing["after_days"][-1], "2026-10-10")
        self.assertEqual(self.calls.count("comment"), 2)
        self.assertNotIn("post", self.calls)

    def test_unchanged_tick_has_no_repeat_comment(self):
        self.run_handler()
        self.run_handler()
        self.assertEqual(self.calls.count("comment"), 1)

    def test_posted_acceptance_cannot_close_with_new_scope(self):
        self.run_handler()
        self.state["billing"]["posted_at"] = "old proof"
        old = copy.deepcopy(self.state["billing"])
        self.add_followup()
        self.run_handler()
        self.assertEqual(self.state["billing"]["posted_at"], old["posted_at"])
        self.assertEqual(self.state["billing"]["due_at"], old["due_at"])
        self.assertIn("scope_drift", self.state["billing"])
        self.assertNotIn("close", self.calls)

    def test_input_added_inside_window_invalidates_old_acceptance(self):
        self.run_handler()
        self.state["billing"]["posted_at"] = "old proof"
        self.add_followup("2026-09-24T12:00:00Z")
        self.run_handler()
        self.assertIn("scope_drift", self.state["billing"])
        self.assertNotIn("close", self.calls)

    def test_pending_or_incomplete_registration_blocks_cached_due_and_posted(self):
        for pending in ("open", "merged", "human_checkpoint", "unknown"):
            with self.subTest(pending=pending):
                self.state["billing"] = dict(posted_at="old", due_at="2026-09-01T00:00:00Z")
                self.state["prs"]["org/repo#1"]["state"] = pending
                self.assertFalse(self.run_handler())
        self.state["prs"]["org/repo#1"]["state"] = "verified"
        self.state["registration_complete"] = False
        self.assertFalse(self.run_handler())
        self.assertNotIn("close", self.calls)
        self.assertNotIn("post", self.calls)

    def test_malformed_timestamps_fail_closed(self):
        for stamp in (None, "", "garbage", "2026-09-24T00:00:00", "2026-09-29T00:00:00Z"):
            with self.subTest(stamp=stamp):
                self.state["prs"]["org/repo#1"]["merged_at"] = stamp
                self.assertFalse(self.run_handler())
        self.assertNotIn("post", self.calls)

    def test_offsets_are_sorted_by_instant(self):
        self.state["billing_followups"] = {key: {**record, "merged_at": "2026-09-23T22:30:00Z"}
            for key, record in self.state["billing_followups"].items()}
        self.state["prs"]["org/repo#1"]["merged_at"] = "2026-09-24T01:00:00+03:00"
        self.state["prs"]["org/repo#2"]["merged_at"] = "2026-09-23T23:00:00Z"
        self.run_handler()
        self.assertEqual(self.state["billing"]["first_merge_at"], "2026-09-23T22:00:00Z")
        self.assertEqual(self.state["billing"]["last_merge_at"], "2026-09-23T23:00:00Z")

    def test_followup_does_not_change_dod_or_owner_hold(self):
        self.state["subs"] = {"921": dict(technical_hold=False, board_done_at="owner")}
        self.state["dod"] = {"proof": dict(met_at="natural")}
        before = copy.deepcopy(self.state)
        self.add_followup()
        self.run_handler()
        for field in ("prs", "subs", "dod"):
            self.assertEqual(self.state[field], before[field])

    def test_followup_requires_verified_scope_and_merge_metadata(self):
        for field, value in (("scope_issue", "wrong"), ("base_ref", "feature"), ("merge_sha", "short")):
            with self.subTest(field=field):
                self.add_followup()
                self.state["billing_followups"]["other/repo#3"][field] = value
                self.assertFalse(self.run_handler())

    def test_only_fresh_due_window_can_post(self):
        self.run_handler()
        self.clock = dt.datetime(2026, 10, 10, 6, tzinfo=UTC)
        self.add_followup()
        self.run_handler()
        self.run_handler()
        self.assertNotIn("post", self.calls)
        self.clock = dt.datetime(2026, 10, 12, 6, tzinfo=UTC)
        self.run_handler()
        self.assertEqual(self.calls.count("post"), 1)

    def test_missing_or_partial_migration_cannot_schedule_or_close(self):
        self.state.pop("billing_followups")
        self.assertFalse(self.run_handler())
        self.state["billing_followups"] = {}
        self.assertFalse(self.run_handler())
        self.add_followup()
        self.assertFalse(self.run_handler())
        self.assertNotIn("post", self.calls)
        self.assertNotIn("comment", self.calls)

    def test_extra_repository_changes_invalidate_published_scope(self):
        self.run_handler()
        self.state["billing"]["posted_at"] = "old proof"
        self.state["billing"]["extra_repos"] = ["extra/repo"]
        self.assertFalse(self.run_handler())
        self.assertIn("scope_drift", self.state["billing"])
        self.assertNotIn("close", self.calls)

    def test_extra_repository_order_and_duplicates_do_not_change_scope(self):
        self.state["billing"] = {"extra_repos": ["extra/a", "extra/b"]}
        self.run_handler()
        self.state["billing"]["extra_repos"] = ["extra/b", "extra/a", "extra/a"]
        self.run_handler()
        self.assertEqual(self.calls.count("comment"), 1)

    def test_invalid_extra_repository_fails_closed(self):
        for extra in ("extra/repo", [None], ["malformed"]):
            self.state["billing"] = {"extra_repos": extra}
            self.assertFalse(self.run_handler())
        self.assertNotIn("post", self.calls)


if __name__ == "__main__":
    unittest.main()
