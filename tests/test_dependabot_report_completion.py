"""Report outcomes must not claim PR completion from a successful scan."""
import unittest
from test_dependabot_v6_merge_gates import load_consumer


class ReportCompletion(unittest.TestCase):
    def report(self, mode="apply", **changes):
        row = dict(repo="fixture/example", open_prs_before=1, open_issues_before=0,
                   dependabot_prs_before=1, non_dependabot_prs_before=0)
        row.update(changes)
        return load_consumer().build_report(mode, [row["repo"]], [row],
            pr_allowlist=set(), approval_note="", approval_issue_url="",
            workflow_url="", validator_profile="maximum_autonomy_v2")

    def test_blocked_prs_are_partial_even_when_scan_succeeds(self):
        report = self.report(blocked=[{"blockers": ["merglbot:review_pending"]}] * 32)
        self.assertTrue(report["ok"])
        self.assertEqual(report["remaining_dependabot_prs"], 32)
        self.assertEqual(report["final_verdict"], "ENT_DEPENDABOT_WEEKLY_CLOSEOUT_PARTIAL")

    def test_allowlist_exclusion_is_not_complete(self):
        report = self.report(skipped_by_allowlist=[{}])
        self.assertEqual(report["final_verdict"], "ENT_DEPENDABOT_WEEKLY_CLOSEOUT_PARTIAL")

    def test_dry_run_never_claims_execution_complete(self):
        for candidates in ([], [{}]):
            with self.subTest(candidates=candidates):
                report = self.report(mode="dry-run", would_merge=candidates)
                self.assertEqual(report["final_verdict"], "ENT_DEPENDABOT_WEEKLY_CLOSEOUT_DRY_RUN")

    def test_runtime_error_takes_precedence(self):
        for mode in ("apply", "dry-run"):
            report = self.report(mode=mode, warnings=["repo_scan_failed"], blocked=[{}])
            self.assertFalse(report["ok"])
            self.assertEqual(report["final_verdict"], "ENT_DEPENDABOT_WEEKLY_CLOSEOUT_BLOCKED")

    def test_completed_actions_remain_complete(self):
        report = self.report(merged=[{}])
        self.assertEqual(report["remaining_dependabot_prs"], 0)
        self.assertEqual(report["final_verdict"], "ENT_DEPENDABOT_WEEKLY_CLOSEOUT_COMPLETE")
