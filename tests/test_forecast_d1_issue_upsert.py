"""Guards for the readiness issue upsert that replaced the Slack alert.

V6 findings on merglbot-core/github#1008 (merglbot-core/infra#3841):
1. `issues: write` must be job-scoped to `run`; `stats_30d` manages no issues.
2. A historical `patch_date_local` PASS must never close the current issue.
3. New issues must be native sub-issues of an EPIC (EPIC standard §5a) and the
   attachment must fail loudly, never silently.
4. A historical FAIL must never create or edit the current issue: only a
   current-period result may create, update or close it.

The structural checks read the workflow text directly (no YAML parser: the CI
runner installs nothing), following tests/test_forecast_d1_schedule_decommission.py.
The behavioural checks run the step's real `run:` script under bash with a stub
`gh` and `date`, so the branch logic itself is executed, not just pattern-matched.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

WORKFLOW = (
    Path(__file__).resolve().parents[1]
    / ".github"
    / "workflows"
    / "forecast-d1-readiness.yml"
)

STEP_NAME = "      - name: Upsert readiness issue (NOT-READY opens, READY closes)"
NEXT_STEP_NAME = "      - name: Fail workflow if guardrail failed"
HISTORICAL_GUARD = 'if ! is_current_period "$period"; then'

FAKE_D1 = "2026-10-17"  # "yesterday" as seen by the stubbed `date -d yesterday`
FAKE_D0 = "2026-10-18"  # "today"
HISTORICAL = "2026-09-30"
CREATED_URL = "https://github.com/merglbot-core/github/issues/4242"
CREATED_DB_ID = "99004242"


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
    start = source.index(STEP_NAME)
    end = source.index(NEXT_STEP_NAME, start)
    return source[start:end]


def _upsert_run_script(source: str) -> str:
    """The step's `run: |` literal block, de-indented exactly as YAML would."""
    lines = _upsert_step(source).split("\n")
    first = lines.index("        run: |") + 1
    body = []
    for line in lines[first:]:
        if line.strip() == "":
            body.append("")
            continue
        if not line.startswith(" " * 10):
            break
        body.append(line[10:])
    return "\n".join(body).rstrip("\n") + "\n"


def _upsert_scope_body(script: str) -> str:
    start = script.index("upsert_scope() {")
    end = script.index("\n}\n", start)
    return script[start:end]


def _historical_branch(scope_body: str) -> str:
    start = scope_body.index(HISTORICAL_GUARD)
    end = scope_body.index("  return 0\n  fi\n", start)
    return scope_body[start:end]


class ForecastD1IssueUpsertStructureTests(unittest.TestCase):
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

    def test_run_script_has_no_expression_interpolation(self):
        # Every runtime value reaches the script through env; the behavioural tests
        # below therefore execute exactly what the runner executes.
        self.assertNotIn("${{", _upsert_run_script(workflow_source()))

    def test_historical_results_never_reach_create_edit_or_close(self):
        script = _upsert_run_script(workflow_source())
        scope = _upsert_scope_body(script)
        self.assertIn('period="$(result_period "$checked")"', scope)
        historical = _historical_branch(scope)
        for mutation in ("gh issue create", "gh issue edit", "gh issue close", "attach_to_epic"):
            self.assertNotIn(mutation, historical)
        self.assertIn("gh issue comment", historical)
        self.assertIn("[historical]", historical)
        self.assertIn("summary_line", historical)
        # Every mutation sits after the historical early return, i.e. on the
        # current-period path only; editing is never used at all.
        guard_end = scope.index(HISTORICAL_GUARD) + len(historical)
        for mutation in ("gh issue create", "gh issue close", 'attach_to_epic "$number" "$url"'):
            self.assertEqual(script.count(mutation), 1, mutation)
            self.assertGreater(scope.index(mutation), guard_end, mutation)
        self.assertNotIn("gh issue edit", script)

    def test_current_period_is_yesterday_or_today_in_prague(self):
        script = _upsert_run_script(workflow_source())
        self.assertIn("CURRENT_D1=\"$(TZ=Europe/Prague date -d 'yesterday' +%F)\"", script)
        self.assertIn('CURRENT_D0="$(TZ=Europe/Prague date +%F)"', script)
        self.assertIn("is_current_period()", script)
        # A PASS closes only with a reported (verified) date.
        close_guard = re.search(r'if \[ -z "\$checked" \]; then\n(?P<body>.*?)\n\s*elif', script, re.S)
        self.assertIsNotNone(close_guard)
        assert close_guard is not None
        self.assertNotIn("gh issue close", close_guard.group("body"))

    def test_every_scope_passes_its_checked_date(self):
        step = _upsert_step(workflow_source())
        self.assertIn("REQUESTED_PATCH_DATE: ${{ inputs.patch_date_local }}", step)
        for scope in ("MAIN", "PROTEINACO", "AUTODOPLNKY"):
            self.assertIn(f"${{{scope}_PATCH_DATE:-}}", step)
            self.assertRegex(
                step,
                rf"(?m)^\s+{scope}_PATCH_DATE: \$\{{\{{ steps\.guardrail\w*\.outputs\.patch_date_local \}}\}}$",
            )

    def test_new_issue_is_attached_to_an_epic_and_failure_is_loud(self):
        step = _upsert_step(workflow_source())
        self.assertRegex(step, r'(?m)^\s+READINESS_PARENT_EPIC: "\d+"')
        self.assertIn('/issues/${READINESS_PARENT_EPIC}/sub_issues" -F "sub_issue_id=${child_id}"', step)
        self.assertIn("--jq .id", step)  # database id, never the issue number
        for marker in ("Sub-issue attachment failed", "Sub-issue readback failed"):
            self.assertIn(marker, step)
        self.assertNotIn('attach_to_epic "$number" "$url" ||', step)
        self.assertIn("do not close by hand", step)

    def test_fail_closed_issue_listing(self):
        step = _upsert_step(workflow_source())
        self.assertIn("gh issue list failed", step)
        self.assertNotIn("--json number,title || true", step)


GH_STUB = r'''
import json
import os
import sys

args = sys.argv[1:]
with open(os.environ["FAKE_GH_LOG"], "a", encoding="utf-8") as log:
    log.write(json.dumps(args) + "\n")

if args[:2] == ["label", "create"]:
    sys.exit(0)
if args[:2] == ["issue", "list"]:
    if os.environ.get("FAKE_LIST_FAIL") == "1":
        sys.stderr.write("HTTP 502\n")
        sys.exit(1)
    sys.stdout.write(os.environ.get("FAKE_OPEN_ISSUES", "[]"))
    sys.exit(0)
if args[:2] == ["issue", "create"]:
    sys.stdout.write(os.environ["FAKE_CREATED_URL"] + "\n")
    sys.exit(0)
if args[:2] in (["issue", "comment"], ["issue", "close"]):
    sys.exit(0)
if args and args[0] == "api":
    rest = args[1:]
    if "-X" in rest and "POST" in rest:
        if os.environ.get("FAKE_ATTACH_FAIL") == "1":
            sys.stderr.write("HTTP 422\n")
            sys.exit(1)
        sys.stdout.write("{}")
        sys.exit(0)
    if "--paginate" in rest:
        if os.environ.get("FAKE_READBACK_MISSING") != "1":
            sys.stdout.write("111\n" + os.environ["FAKE_CREATED_DB_ID"] + "\n")
        sys.exit(0)
    if any(a.endswith("/issues/" + os.environ["FAKE_CREATED_URL"].rsplit("/", 1)[1]) for a in rest):
        sys.stdout.write(os.environ["FAKE_CREATED_DB_ID"] + "\n")
        sys.exit(0)
sys.stderr.write("unexpected gh call: %r\n" % (args,))
sys.exit(97)
'''

DATE_STUB = r'''
import os
import sys

sys.stdout.write((os.environ["FAKE_D1"] if "-d" in sys.argv[1:] else os.environ["FAKE_D0"]) + "\n")
'''


class ForecastD1IssueUpsertBehaviourTests(unittest.TestCase):
    """Execute the real run script with stubbed `gh`/`date` and inspect the calls."""

    @classmethod
    def setUpClass(cls):
        cls.bash = shutil.which("bash")
        cls.jq = shutil.which("jq")
        # Required tools are preinstalled on GitHub-hosted runners; a missing one is
        # a loud failure, never a silent skip of this coverage.
        assert cls.bash, "bash is required for the behavioural upsert tests"
        assert cls.jq, "jq is required for the behavioural upsert tests"
        cls.script = _upsert_run_script(workflow_source())

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="upsert-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        for name, body in (("gh", GH_STUB), ("date", DATE_STUB)):
            stub = self.bin / name
            stub.write_text(f"#!{sys.executable}\n{body}", encoding="utf-8")
            stub.chmod(0o755)
        (self.bin / "python").symlink_to(sys.executable)
        self.script_path = self.tmp / "upsert.sh"
        self.script_path.write_text(self.script, encoding="utf-8")
        self.outdir = self.tmp / "out"
        self.outdir.mkdir()
        self.log = self.tmp / "gh.log"
        self.summary = self.tmp / "summary.md"

    def run_upsert(self, status, checked, requested="", open_issues=(), **fake):
        env = {
            "PATH": f"{self.bin}{os.pathsep}{os.environ.get('PATH', '')}",
            "HOME": str(self.tmp),
            "TMPDIR": str(self.tmp),
            "GITHUB_STEP_SUMMARY": str(self.summary),
            "GH_REPO": "merglbot-core/github",
            "READINESS_PARENT_EPIC": "769",
            "RUN_URL": "https://github.com/merglbot-core/github/actions/runs/1",
            "REQUESTED_PATCH_DATE": requested,
            "MAIN_STATUS": status,
            "MAIN_OUTDIR": str(self.outdir),
            "MAIN_SCOPE_LABEL": "core",
            "MAIN_PATCH_DATE": checked,
            "FAKE_GH_LOG": str(self.log),
            "FAKE_D1": FAKE_D1,
            "FAKE_D0": FAKE_D0,
            "FAKE_CREATED_URL": CREATED_URL,
            "FAKE_CREATED_DB_ID": CREATED_DB_ID,
            "FAKE_OPEN_ISSUES": json.dumps(
                [{"number": n, "title": "[final-data] D1 readiness: core"} for n in open_issues]
            ),
        }
        env.update({key: str(value) for key, value in fake.items()})
        proc = subprocess.run(
            [self.bash, "--noprofile", "--norc", "-eo", "pipefail", str(self.script_path)],
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )
        calls = []
        if self.log.exists():
            calls = [json.loads(line) for line in self.log.read_text(encoding="utf-8").splitlines()]
        summary = self.summary.read_text(encoding="utf-8") if self.summary.exists() else ""
        return proc, calls, summary

    @staticmethod
    def issue_calls(calls, verb):
        return [c for c in calls if c[:2] == ["issue", verb]]

    def assert_no_mutation(self, calls):
        for verb in ("create", "edit", "close"):
            self.assertEqual(self.issue_calls(calls, verb), [], verb)
        self.assertEqual([c for c in calls if c[:1] == ["api"] and "POST" in c], [])

    def test_historical_fail_without_issue_writes_summary_only(self):
        proc, calls, summary = self.run_upsert("FAIL", HISTORICAL)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assert_no_mutation(calls)
        self.assertEqual(self.issue_calls(calls, "comment"), [])
        self.assertIn(f"`core` ({HISTORICAL}): **FAIL** — historical result", summary)

    def test_historical_fail_with_open_issue_only_comments_historical(self):
        proc, calls, _ = self.run_upsert("FAIL", HISTORICAL, open_issues=(7,))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assert_no_mutation(calls)
        comments = self.issue_calls(calls, "comment")
        self.assertEqual(len(comments), 1)
        self.assertEqual(comments[0][2], "7")
        self.assertIn("[historical] FAIL", " ".join(comments[0]))

    def test_historical_pass_never_closes_the_current_issue(self):
        proc, calls, summary = self.run_upsert("PASS", HISTORICAL, open_issues=(7,))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assert_no_mutation(calls)
        self.assertIn("[historical] PASS", " ".join(self.issue_calls(calls, "comment")[0]))
        self.assertIn("historical result", summary)

    def test_crash_with_historical_requested_date_creates_nothing(self):
        proc, calls, _ = self.run_upsert("FAIL", "", requested=HISTORICAL)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assert_no_mutation(calls)

    def test_current_fail_creates_issue_and_attaches_it_to_the_epic(self):
        # A summary file exercises the body's embedded python block as well.
        (self.outdir / "forecast_d1_readiness_summary.json").write_text(
            json.dumps({"slot": "d1", "required_failed": 2, "required_total": 9}), encoding="utf-8"
        )
        proc, calls, summary = self.run_upsert("FAIL", FAKE_D1)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(len(self.issue_calls(calls, "create")), 1)
        posts = [c for c in calls if c[:1] == ["api"] and "POST" in c]
        self.assertEqual(len(posts), 1)
        self.assertIn("repos/merglbot-core/github/issues/769/sub_issues", posts[0])
        self.assertIn(f"sub_issue_id={CREATED_DB_ID}", posts[0])
        self.assertTrue([c for c in calls if c[:1] == ["api"] and "--paginate" in c])
        self.assertIn(f"opened {CREATED_URL}", summary)

    def test_current_fail_for_today_or_a_default_run_crash_also_alerts(self):
        for checked in (FAKE_D0, ""):  # "" = guardrail crashed in a default run
            with self.subTest(checked=checked):
                self.log.unlink(missing_ok=True)
                proc, calls, _ = self.run_upsert("FAIL", checked)
                self.assertEqual(proc.returncode, 0, proc.stderr)
                self.assertEqual(len(self.issue_calls(calls, "create")), 1)

    def test_current_fail_with_open_issue_updates_by_comment(self):
        proc, calls, _ = self.run_upsert("FAIL", FAKE_D1, open_issues=(7,))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assert_no_mutation(calls)
        comments = self.issue_calls(calls, "comment")
        self.assertEqual(len(comments), 1)
        self.assertIn("--body-file", comments[0])

    def test_current_pass_closes_the_open_issue(self):
        proc, calls, summary = self.run_upsert("PASS", FAKE_D1, open_issues=(7,))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        closes = self.issue_calls(calls, "close")
        self.assertEqual(len(closes), 1)
        self.assertEqual(closes[0][2], "7")
        self.assertIn("completed", closes[0])
        self.assertIn("closed #7", summary)

    def test_pass_without_reported_date_never_closes(self):
        proc, calls, _ = self.run_upsert("PASS", "", open_issues=(7,))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assert_no_mutation(calls)

    def test_failed_attachment_fails_loudly_with_the_issue_url(self):
        proc, _, _ = self.run_upsert("FAIL", FAKE_D1, FAKE_ATTACH_FAIL=1)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("Sub-issue attachment failed", proc.stdout + proc.stderr)
        self.assertIn(CREATED_URL, proc.stdout + proc.stderr)

    def test_missing_readback_fails_loudly(self):
        proc, _, _ = self.run_upsert("FAIL", FAKE_D1, FAKE_READBACK_MISSING=1)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("Sub-issue readback failed", proc.stdout + proc.stderr)
        self.assertIn(CREATED_URL, proc.stdout + proc.stderr)

    def test_failed_issue_listing_is_an_error_not_an_empty_backlog(self):
        proc, calls, _ = self.run_upsert("FAIL", FAKE_D1, FAKE_LIST_FAIL=1)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("gh issue list failed", proc.stdout + proc.stderr)
        self.assertEqual(self.issue_calls(calls, "create"), [])


if __name__ == "__main__":
    unittest.main()
