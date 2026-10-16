"""Structural guards for the readiness issue upsert that replaced the Slack alert.

V6 findings on merglbot-core/github#1008 (merglbot-core/infra#3841):
1. `issues: write` must be job-scoped to `run`; `stats_30d` manages no issues.
2. A historical `patch_date_local` PASS must never close the current issue.
3. New issues must be native sub-issues of an EPIC (EPIC standard §5a) and the
   attachment must fail loudly, never silently.
"""

import re
import unittest
from pathlib import Path

import yaml

WORKFLOW = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "forecast-d1-readiness.yml"


def _load():
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _upsert_step():
    for step in _load()["jobs"]["run"]["steps"]:
        if step.get("name", "").startswith("Upsert readiness issue"):
            return step
    raise AssertionError("upsert step missing")


class ForecastD1IssueUpsertTests(unittest.TestCase):
    def test_issues_write_is_job_scoped_to_run_only(self):
        doc = _load()
        self.assertEqual(doc["permissions"], {"contents": "read", "id-token": "write"})
        self.assertEqual(doc["jobs"]["run"]["permissions"]["issues"], "write")
        self.assertEqual(doc["jobs"]["run"]["permissions"]["contents"], "read")
        self.assertEqual(doc["jobs"]["run"]["permissions"]["id-token"], "write")
        self.assertNotIn("issues", doc["jobs"]["stats_30d"].get("permissions") or {})

    def test_upsert_runs_only_outside_dry_run_and_uses_repo_token(self):
        step = _upsert_step()
        self.assertIn("inputs.dry_run != true", step["if"])
        self.assertEqual(step["env"]["GH_TOKEN"], "${{ github.token }}")

    def test_close_requires_current_period_and_historical_pass_only_comments(self):
        run = _upsert_step()["run"]
        self.assertIn("is_current_period()", run)
        self.assertRegex(run, r'CURRENT_D1="\$\(TZ=Europe/Prague date -d \'yesterday\' \+%F\)"')
        # The close branch is guarded by the current-period predicate...
        close_branch = re.search(r'elif is_current_period "\$checked"; then\n(?P<body>.*?)\n\s*else', run, re.S)
        self.assertIsNotNone(close_branch)
        assert close_branch is not None
        self.assertIn("gh issue close", close_branch.group("body"))
        # ...and the historical branch comments and never closes.
        hist = run.split('elif is_current_period "$checked"; then', 1)[1].split("else", 2)[1]
        self.assertIn("gh issue comment", hist)
        self.assertNotIn("gh issue close", hist)
        self.assertEqual(run.count("gh issue close"), 1)
        # Every scope passes its checked date to the upsert.
        for scope in ("MAIN", "PROTEINACO", "AUTODOPLNKY"):
            self.assertIn(f"${{{scope}_PATCH_DATE:-}}", run)
            self.assertIn(f"{scope}_PATCH_DATE", _upsert_step()["env"])

    def test_new_issue_is_attached_to_an_epic_and_failure_is_loud(self):
        step = _upsert_step()
        run = step["run"]
        self.assertRegex(step["env"]["READINESS_PARENT_EPIC"], r"^\d+$")
        self.assertIn("attach_to_epic", run)
        self.assertIn('/issues/${READINESS_PARENT_EPIC}/sub_issues" -F "sub_issue_id=${child_id}"', run)
        self.assertIn('--jq .id', run)  # database id, never the issue number
        # readback of the parent's sub-issue list
        self.assertIn('/sub_issues" --jq \'.[].id\' | grep -qx "$child_id"', run)
        for marker in ("Sub-issue attachment failed", "Sub-issue readback failed"):
            self.assertIn(marker, run)
        # attachment happens right after creation, and a failure exits non-zero
        create_idx = run.index("gh issue create")
        self.assertGreater(run.index('attach_to_epic "$number" "$url"'), create_idx)
        self.assertNotIn("attach_to_epic \"$number\" \"$url\" ||", run)
        self.assertIn("do not close by hand", run)

    def test_fail_closed_issue_listing(self):
        run = _upsert_step()["run"]
        self.assertIn("gh issue list failed", run)
        self.assertNotIn("gh issue list --label \"$LABEL\" --state open --limit 100 --json number,title || true", run)


if __name__ == "__main__":
    unittest.main()
