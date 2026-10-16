"""Structural guards for the readiness issue upsert that replaced the Slack alert.

V6 findings on merglbot-core/github#1008 (merglbot-core/infra#3841):
1. `issues: write` must be job-scoped to `run`; `stats_30d` manages no issues.
2. A historical `patch_date_local` PASS must never close the current issue.
3. New issues must be native sub-issues of an EPIC (EPIC standard §5a) and the
   attachment must fail loudly, never silently.

The checks read the workflow text directly (no YAML parser: the CI runner
installs nothing), following tests/test_forecast_d1_schedule_decommission.py.
"""

import re
import unittest
from pathlib import Path

WORKFLOW = (
    Path(__file__).resolve().parents[1]
    / ".github"
    / "workflows"
    / "forecast-d1-readiness.yml"
)


def workflow_source() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def _permission_entries(block: str, indent: str) -> dict:
    """Parse `<indent>key: value  # comment` lines of a permissions block."""
    return {
        key: value
        for key, value in re.findall(
            rf"(?m)^{indent}([a-zA-Z0-9_-]+):\s*([^#\n]+?)\s*(?:#.*)?$", block
        )
    }


def _workflow_permissions_block(source: str) -> str:
    match = re.search(r"(?ms)^permissions:\n(?P<body>.*?)(?=^[a-zA-Z0-9_-]+:)", source)
    assert match is not None, "workflow-level permissions block missing"
    return match.group("body")


def _job_block(source: str, job: str) -> str:
    match = re.search(rf"(?ms)^  {re.escape(job)}:\n(?P<body>.*?)(?=^  [a-zA-Z0-9_-]+:$|\Z)", source)
    assert match is not None, f"job {job} missing"
    return match.group("body")


def _job_permissions_block(job_block: str) -> str:
    match = re.search(r"(?ms)^    permissions:\n(?P<body>.*?)(?=^    [a-zA-Z0-9_-]+:)", job_block)
    return match.group("body") if match else ""


def _upsert_step(source: str) -> str:
    start = source.index("      - name: Upsert readiness issue (NOT-READY opens, READY closes)")
    end = source.index("      - name: Fail workflow if guardrail failed", start)
    return source[start:end]


class ForecastD1IssueUpsertTests(unittest.TestCase):
    def test_issues_write_is_job_scoped_to_run_only(self):
        source = workflow_source()
        self.assertEqual(
            _permission_entries(_workflow_permissions_block(source), "  "),
            {"contents": "read", "id-token": "write"},
        )
        run_perms = _permission_entries(_job_permissions_block(_job_block(source, "run")), "      ")
        self.assertEqual(run_perms, {"contents": "read", "id-token": "write", "issues": "write"})
        stats_perms = _permission_entries(_job_permissions_block(_job_block(source, "stats_30d")), "      ")
        self.assertNotIn("issues", stats_perms)
        self.assertNotIn("issues: write", _job_block(source, "stats_30d"))

    def test_upsert_runs_only_outside_dry_run_and_uses_repo_token(self):
        step = _upsert_step(workflow_source())
        self.assertIn("inputs.dry_run != true &&", step)
        self.assertIn("GH_TOKEN: ${{ github.token }}", step)

    def test_close_requires_current_period_and_historical_pass_only_comments(self):
        step = _upsert_step(workflow_source())
        self.assertIn("is_current_period()", step)
        self.assertRegex(step, r"CURRENT_D1=\"\$\(TZ=Europe/Prague date -d 'yesterday' \+%F\)\"")
        # The close branch is guarded by the current-period predicate...
        close_branch = re.search(
            r'elif is_current_period "\$checked"; then\n(?P<body>.*?)\n\s*else\n', step, re.S
        )
        self.assertIsNotNone(close_branch)
        assert close_branch is not None
        self.assertIn("gh issue close", close_branch.group("body"))
        # ...and the historical branch comments and never closes.
        historical = step.split('elif is_current_period "$checked"; then', 1)[1]
        historical = historical.split("else\n", 1)[1].split("fi\n", 1)[0]
        self.assertIn("gh issue comment", historical)
        self.assertNotIn("gh issue close", historical)
        self.assertEqual(step.count("gh issue close"), 1)
        # Every scope passes its checked date to the upsert.
        for scope in ("MAIN", "PROTEINACO", "AUTODOPLNKY"):
            self.assertIn(f"${{{scope}_PATCH_DATE:-}}", step)
            self.assertRegex(step, rf"(?m)^\s+{scope}_PATCH_DATE: \$\{{\{{ steps\.guardrail\w*\.outputs\.patch_date_local \}}\}}$")

    def test_new_issue_is_attached_to_an_epic_and_failure_is_loud(self):
        step = _upsert_step(workflow_source())
        self.assertRegex(step, r'(?m)^\s+READINESS_PARENT_EPIC: "\d+"')
        self.assertIn("attach_to_epic", step)
        self.assertIn('/issues/${READINESS_PARENT_EPIC}/sub_issues" -F "sub_issue_id=${child_id}"', step)
        self.assertIn("--jq .id", step)  # database id, never the issue number
        # readback of the parent's sub-issue list
        self.assertIn('/sub_issues" --jq \'.[].id\' | grep -qx "$child_id"', step)
        for marker in ("Sub-issue attachment failed", "Sub-issue readback failed"):
            self.assertIn(marker, step)
        # attachment happens right after creation, and a failure exits non-zero
        create_idx = step.index("gh issue create")
        self.assertGreater(step.index('attach_to_epic "$number" "$url"'), create_idx)
        self.assertNotIn('attach_to_epic "$number" "$url" ||', step)
        self.assertIn("do not close by hand", step)

    def test_fail_closed_issue_listing(self):
        step = _upsert_step(workflow_source())
        self.assertIn("gh issue list failed", step)
        self.assertNotIn("--json number,title || true", step)


if __name__ == "__main__":
    unittest.main()
