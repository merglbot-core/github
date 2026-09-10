import importlib.util
import base64
import copy
import json
from pathlib import Path
import subprocess
import unittest
from unittest.mock import MagicMock, patch

spec = importlib.util.spec_from_file_location("pilot_api", Path(__file__).resolve().parents[1] / "scripts/ci-pilot/github_client.py")
c = importlib.util.module_from_spec(spec)
spec.loader.exec_module(c)
NOW = c.instant("2026-09-10T20:00:00Z")
RECEIPT = {"repo": c.REPOS[0], "pr": 123, "head": "a" * 40, "base": "b" * 40}


def make_run(**overrides):
    return {"id": 7, "run_attempt": 1, "created_at": NOW.isoformat(), "head_sha": RECEIPT["head"],
            "run_started_at": NOW.isoformat(), "path": c.WORKFLOWS[c.REPOS[0]], "event": "pull_request",
            "status": "completed", "pull_requests": [{"number": 123, "head": {"sha": RECEIPT["head"]},
            "base": {"sha": RECEIPT["base"]}}], **overrides}


class GitHubTests(unittest.TestCase):
    def test_interval_end_excludes_later_attempt_without_job_reads(self):
        gh = c.GitHub()
        run = make_run()
        gh.pages = lambda path, *args: [run] if "/actions/runs?" in path else self.fail("job read past interval")
        gh.api = lambda path: run
        result = gh.measurements(RECEIPT, NOW.isoformat(), NOW.isoformat())
        self.assertEqual(0, result["attempts"])

    def test_completed_runner_missing_time_is_a_gap(self):
        gh = c.GitHub()
        run = make_run()
        job = {"id": 9, "name": "unit-tests", "runner_id": 8, "steps": [], "conclusion": "success",
               "started_at": None, "completed_at": None}
        gh.pages = lambda path, *args: [job] if path.endswith("/jobs") else [run]
        gh.api = lambda path: [] if path.endswith("/pending_deployments") else run
        result = gh.measurements(RECEIPT, NOW.isoformat())
        self.assertEqual(1, result["runner_evidence_gaps"])
        self.assertEqual(0, result["cancelled_without_runner"])

    def test_selector_requires_exact_trusted_workflow_bytes(self):
        workflow = "name: fixture\non: pull_request\njobs:\n  test:\n    runs-on: ubuntu-latest\n    environment: ci-pr-delay\n    steps:\n      - run: echo test\n"
        gh = c.GitHub()
        pr = {"head": {"sha": RECEIPT["head"]}, "base": {"sha": RECEIPT["base"]}, "changed_files": 0}
        gh.pages = lambda *args: []
        gh.command = lambda *args, **kwargs: "diff"
        variants = [workflow, "# " + workflow, workflow.replace("environment:", "unrelated:"),
                    workflow.replace("ci-pr-delay", "ci-immediate")]
        with patch.dict(c.TRUSTED_WORKFLOW_SHA256, {RECEIPT["repo"]: c.digest(workflow)}):
            for index, content in enumerate(variants):
                gh.api = lambda path: ({"content": base64.b64encode(content.encode()).decode()}
                                       if "/contents/" in path else pr if "/pulls/" in path else {})
                self.assertEqual(gh.snapshot(RECEIPT)["selector_supported"], index == 0)

    def test_mutation_is_scoped_and_guarded(self):
        gh = c.GitHub()
        gh.command = MagicMock()
        gh.mutate(c.REPOS[0], c.SHA_VAR, "a" * 40)
        args, options = gh.command.call_args.args[0], gh.command.call_args.kwargs
        self.assertEqual(args[:4], ["/bin/bash", str(c.GUARD), "gh", "variable"])
        self.assertIn("set", args)
        self.assertEqual(options["env"]["CODEX_GUARD_ALLOW_GITHUB_MUTATION"], "1")
        gh.mutate(c.REPOS[0], c.PR_VAR)
        self.assertIn("delete", gh.command.call_args.args[0])
        for repo, name in (("other/repo", c.PR_VAR), (c.REPOS[0], "UNRELATED_VARIABLE")):
            with self.assertRaises(c.Gap):
                gh.mutate(repo, name)
        self.assertEqual(gh.command.call_count, 2)
        gh.mutate(c.REPOS[1], c.BASE_VAR, "b" * 40)
        self.assertIn(c.BASE_VAR, gh.command.call_args.args[0])
        gh.pages = lambda *args, **kwargs: [{"name": c.BASE_VAR, "value": "b" * 40}]
        self.assertEqual(gh.selectors(c.REPOS[1]), {c.BASE_VAR: "b" * 40})

    def test_api_error_is_a_gap_without_raw_output(self):
        failed = subprocess.CompletedProcess([], 1, "private stdout", "private stderr")
        with patch.object(c.subprocess, "run", return_value=failed):
            with self.assertRaisesRegex(c.Gap, "^github_command_failed$"):
                c.GitHub().api("repos/example/example")
        invalid = subprocess.CompletedProcess([], 0, "not JSON", "")
        with patch.object(c.subprocess, "run", return_value=invalid):
            with self.assertRaisesRegex(c.Gap, "^invalid_api_json$"):
                c.GitHub().api("repos/example/example")

    def test_timeout_discards_captured_output(self):
        error = subprocess.TimeoutExpired(["gh"], 90, output="synthetic stdout", stderr="synthetic stderr")
        with patch.object(c.subprocess, "run", side_effect=error):
            with self.assertRaisesRegex(c.Gap, "^github_command_timeout$") as caught:
                c.GitHub().api("repos/example/example")
        self.assertIsNone(error.output)
        self.assertIsNone(error.stderr)
        self.assertTrue(caught.exception.__suppress_context__)

    def test_partial_pagination_and_file_list_are_gaps(self):
        gh = c.GitHub()
        gh.api = lambda *args: {"variables": [], "total_count": 1}
        with self.assertRaisesRegex(c.Gap, "incomplete_pagination"):
            gh.selectors(c.REPOS[0])
        gh.api = lambda *args: {"changed_files": 2, "head": {"sha": RECEIPT["head"]}, "base": {"sha": RECEIPT["base"]}}
        gh.pages = lambda *args: [{"filename": "one.py"}]
        with self.assertRaisesRegex(c.Gap, "incomplete_files"):
            gh.snapshot(RECEIPT)

    def test_wait_timer_and_runner_zero_evidence(self):
        gh = c.GitHub()
        run = make_run()
        job = {"id": 9, "name": "unit-tests", "runner_id": 0, "steps": [], "conclusion": "cancelled",
               "started_at": None, "completed_at": None}
        gh.pages = lambda path, *args: [job] if path.endswith("/jobs") else [run]
        gh.api = lambda path: ([{"environment": {"name": c.ENVIRONMENT}, "wait_timer": 10,
                                 "wait_timer_started_at": NOW.isoformat()}] if path.endswith("/pending_deployments") else run)
        metrics = gh.measurements(RECEIPT, NOW.isoformat())
        self.assertEqual(metrics["cancelled_without_runner"], 1)
        self.assertEqual(metrics["runner_seconds"], 0)
        observation = metrics["observations"][0]
        self.assertEqual(observation["wait_timers"][0]["started_at"], NOW.isoformat())
        self.assertEqual(observation["wait_timers"][0]["wait_timer"], 10)
        self.assertEqual(observation["jobs"][0]["steps_count"], 0)
        self.assertEqual(observation["jobs"][0]["name"], "unit-tests")
        run["path"] += "@refs/pull/123/merge"
        self.assertEqual(gh.measurements(RECEIPT, NOW.isoformat())["runs"], 1)
        run["path"] = ".github/workflows/unrelated.yml@refs/pull/123/merge"
        self.assertEqual(gh.measurements(RECEIPT, NOW.isoformat())["runs"], 0)
        run["path"] = c.WORKFLOWS[c.REPOS[0]]
        job["runner_id"] = None
        metrics = gh.measurements(RECEIPT, NOW.isoformat())
        self.assertEqual(metrics["runner_evidence_gaps"], 1)
        self.assertEqual(metrics["cancelled_without_runner"], 0)
        self.assertIsNone(metrics["observations"][0]["jobs"][0]["runner_id"])
        job["runner_id"] = 0
        job.pop("steps")
        with self.assertRaisesRegex(c.Gap, "job_runner_evidence_missing"):
            gh.measurements(RECEIPT, NOW.isoformat())

    def test_attempt_jobs_are_separate_and_old_run_rerun_is_admitted(self):
        gh = c.GitHub()
        old = "2026-09-09T20:00:00Z"
        base = make_run(created_at=old)
        attempts = {n: {**base, "run_attempt": n, "run_started_at": NOW.isoformat()} for n in (1, 2)}
        calls = []
        def pages(path, *args):
            calls.append(path)
            if path.endswith("/jobs"):
                number = int(path.split("/")[-2])
                return [{"id": number, "name": "test", "runner_id": 0, "steps": [],
                         "conclusion": "cancelled", "started_at": None, "completed_at": None}]
            return [attempts[2]]
        gh.pages = pages
        gh.api = lambda path: ([] if path.endswith("/pending_deployments") else
                               attempts[int(path.rsplit("/", 1)[1])] if "/attempts/" in path else attempts[2])
        metrics = gh.measurements(RECEIPT, NOW.isoformat())
        self.assertEqual([(o["attempt"], o["jobs"][0]["id"]) for o in metrics["observations"]], [(1, 1), (2, 2)])
        self.assertEqual((metrics["runs"], metrics["attempts"]), (1, 2))
        self.assertFalse(any("filter=all" in path for path in calls))
        attempts[1]["run_started_at"] = old
        metrics = gh.measurements(RECEIPT, NOW.isoformat())
        self.assertEqual([o["attempt"] for o in metrics["observations"]], [2])
        attempts[2]["pull_requests"][0].update(head={"sha": "d" * 40}, base={"sha": "e" * 40})
        metrics = gh.measurements(RECEIPT, NOW.isoformat())
        self.assertEqual(metrics["attempts"], 1)
        self.assertEqual(metrics["observations"][0]["actual_base"], "DATA_GAP")
        attempts[2]["head_sha"] = "f" * 40
        with self.assertRaisesRegex(c.Gap, "run_pr_binding_changed"):
            gh.measurements(RECEIPT, NOW.isoformat())

    def test_closed_run_uses_only_unique_commit_association(self):
        gh = c.GitHub()
        run = make_run(head_branch="fix/example", pull_requests=[])
        associated = [{"number": 123, "state": "closed", "head": {"ref": "fix/example", "sha": "d" * 40}}]
        gh.pages = lambda path, *args: (associated if "/commits/" in path else [] if path.endswith("/jobs") else [run])
        gh.api = lambda path: [] if path.endswith("/pending_deployments") else run
        self.assertEqual(gh.measurements(RECEIPT, NOW.isoformat())["runs"], 1)
        associated.append({"number": 124})
        with self.assertRaisesRegex(c.Gap, "commit_pr_binding_ambiguous"):
            gh.measurements(RECEIPT, NOW.isoformat())
        associated.pop()
        associated[0]["head"]["ref"] = "other-branch"
        with self.assertRaisesRegex(c.Gap, "commit_pr_branch_changed"):
            gh.measurements(RECEIPT, NOW.isoformat())

    def test_snapshot_rejects_stale_receipt_before_other_reads_and_after_reread(self):
        for field in ("head", "base"):
            for stale_first in (True, False):
                with self.subTest(field=field, stale_first=stale_first):
                    gh = c.GitHub()
                    pr = {"head": {"sha": RECEIPT["head"]}, "base": {"sha": RECEIPT["base"]}, "changed_files": 0}
                    stale = copy.deepcopy(pr)
                    stale[field]["sha"] = "d" * 40
                    reads = []
                    def api(path):
                        reads.append(path)
                        if "/pulls/" in path:
                            return stale if stale_first or reads.count(path) > 1 else pr
                        if "/contents/" in path:
                            return {"content": base64.b64encode(b"workflow").decode()}
                        return {}
                    gh.api = api
                    gh.pages = MagicMock(return_value=[])
                    gh.command = MagicMock(return_value="diff")
                    with self.assertRaisesRegex(c.Gap, "stale_receipt" if stale_first else "snapshot_race"):
                        gh.snapshot(RECEIPT)
                    if stale_first:
                        gh.pages.assert_not_called()
                        gh.command.assert_not_called()

    def test_pagination_uses_actual_thirty_item_cap(self):
        gh = c.GitHub()
        calls = []
        def api(path):
            calls.append(path)
            if "page=1" in path:
                return {"variables": [{"name": str(i), "value": ""} for i in range(30)], "total_count": 31}
            return {"variables": [{"name": c.PR_VAR, "value": "123"}], "total_count": 31}
        gh.api = api
        self.assertEqual(gh.selectors(c.REPOS[0]), {c.PR_VAR: "123"})
        self.assertEqual(len(calls), 2)
        self.assertTrue(all("per_page=30" in path for path in calls))

    def test_run_must_bind_exact_pr_and_immutable_head(self):
        gh = c.GitHub()
        run = {"created_at": NOW.isoformat(), "path": c.WORKFLOWS[c.REPOS[0]],
               "event": "pull_request", "pull_requests": [{"number": 999}]}
        gh.pages = lambda *args: [run]
        self.assertEqual(gh.measurements(RECEIPT, NOW.isoformat())["runs"], 0)
        run["pull_requests"] = []
        run["head_sha"] = RECEIPT["head"]
        gh.pages = lambda path, *args: [] if "/commits/" in path else [run]
        with self.assertRaises(c.Gap):
            gh.measurements(RECEIPT, NOW.isoformat())
        run["pull_requests"] = [{"number": 123}]
        run["head_sha"] = "d" * 40
        with self.assertRaises(c.Gap):
            gh.measurements(RECEIPT, NOW.isoformat())


if __name__ == "__main__":
    unittest.main()
