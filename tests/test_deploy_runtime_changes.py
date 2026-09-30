"""Behaviour tests for scripts/deploy-guard/runtime_changes.sh (merglbot-core/github#909).

Drive the REAL script (bash >= 4.4, real jq and git) against temp repos with a stub `gh` on PATH.
GITHUB_OUTPUT must hold exactly one decision; exit is always 0. Skipped without bash>=4.4/jq/git.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "deploy-guard" / "runtime_changes.sh"
NOPE = "0123456789abcdef0123456789abcdef01234567"
RUNS = "repos/o/r/actions/workflows/deploy.yml/runs"
GH_STUB = """#!{py}
import json, os, sys
open(os.environ["GH_LOG"], "a").write(sys.argv[2] + "\\n")
r = json.load(open(os.environ["GH_ROUTES"])).get(sys.argv[2].split("?")[0])
if r is None or "error" in r:
    sys.exit("HTTP 502")
sys.stdout.write(r.get("raw") or json.dumps(r["json"]))
"""


def find_bash() -> str | None:
    probe = "((BASH_VERSINFO * 100 + BASH_VERSINFO[1] >= 404))"
    for cand in (shutil.which("bash"), "/opt/homebrew/bin/bash", "/usr/local/bin/bash"):
        if cand and subprocess.run([cand, "-c", probe]).returncode == 0:
            return cand
    return None


BASH = find_bash()


def run(sha: str, rid: int, event: str = "workflow_run", marker: bool = True) -> dict:
    return {"id": rid, "event": event, "html_url": f"https://x/runs/{rid}",
            "head_sha": NOPE if event == "workflow_run" and not marker else sha,
            "display_title": f"Deploy r @ {sha}" if marker else "security-gate"}


def jobs(*pairs: tuple[str, str]) -> dict:
    return {"total_count": len(pairs), "jobs": [{"name": n, "conclusion": c} for n, c in pairs]}


OK = jobs(("test", "success"), ("deploy", "success"))


@unittest.skipUnless(BASH and shutil.which("jq") and shutil.which("git"), "needs bash>=4.4, jq, git")
class RuntimeChangesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.repo = self.tmp / "repo"
        self.git("init", "-q", "-b", "main", str(self.repo), cwd=self.tmp)
        self.git("config", "user.email", "t@example.com")
        self.git("config", "user.name", "t")
        self.base = self.commit({"src/app.py": "v1", "README.md": "r"})
        (self.tmp / "bin").mkdir()
        (self.tmp / "bin" / "gh").write_text(GH_STUB.format(py=sys.executable))
        (self.tmp / "bin" / "gh").chmod(0o755)

    def git(self, *args: str, cwd: Path | None = None) -> str:
        return subprocess.run(["git", *args], cwd=cwd or self.repo, check=True,
                              capture_output=True, text=True).stdout.strip()

    def commit(self, files: dict[str, str | None]) -> str:
        for name, content in files.items():
            target = self.repo / name
            if content is None:
                target.unlink()
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content, encoding="utf-8")
        self.git("add", "-A")
        self.git("commit", "-q", "--allow-empty", "-m", "c")
        return self.git("rev-parse", "HEAD")

    def guard(self, deploy: str, runs=(), jobs_by_id=None, routes=None, **env: str | None) -> dict:
        table = {RUNS: {"json": {"workflow_runs": list(runs)}}}
        table.update({f"repos/o/r/actions/runs/{k}/jobs": {"json": v} for k, v in (jobs_by_id or {}).items()})
        table.update(routes or {})
        t = self.tmp
        (t / "routes.json").write_text(json.dumps(table))
        for name in ("out", "summary", "gh.log"):
            (t / name).write_text("")
        (t / "rt").mkdir(exist_ok=True)
        full = {"PATH": f"{t / 'bin'}{os.pathsep}{os.environ['PATH']}", "GITHUB_REPOSITORY": "o/r",
                "RUNNER_TEMP": str(t / "rt"), "GITHUB_OUTPUT": str(t / "out"),
                "GITHUB_STEP_SUMMARY": str(t / "summary"), "GH_ROUTES": str(t / "routes.json"),
                "GH_LOG": str(t / "gh.log"), "RC_DEPLOY_SHA": deploy, "RC_WORKFLOW_FILE": "deploy.yml",
                "RC_RUN_ID": "999", "RC_DEPLOY_JOB_REGEX": "^deploy( \\(.*\\))?$", **env}
        proc = subprocess.run([BASH, str(SCRIPT)], cwd=self.repo, capture_output=True, text=True,
                              env={k: v for k, v in full.items() if v is not None}, timeout=300)
        self.assertEqual(0, proc.returncode, proc.stderr)
        lines = (t / "out").read_text(encoding="utf-8").splitlines()
        self.assertEqual(["runtime", "reason", "base-sha"], [x.split("=", 1)[0] for x in lines], lines)
        return dict(x.split("=", 1) for x in lines) | {
            "summary": (t / "summary").read_text(encoding="utf-8"), "stdout": proc.stdout,
            "gh": (t / "gh.log").read_text(encoding="utf-8").splitlines()}

    def deployed(self, base: str, deploy: str, **env: str | None) -> dict:
        return self.guard(deploy, [run(base, 10)], {10: OK}, **env)

    def expect(self, result: dict, runtime: str, reason: str) -> None:
        self.assertEqual((runtime, reason), (result["runtime"], result["reason"]), result["stdout"])

    # --- decision table, in order ---------------------------------------------------------

    def test_manual_rerun_and_dry_run_make_no_api_calls(self) -> None:
        head = self.commit({"src/app.py": "v2"})
        self.expect(self.guard(head, RC_EVENT="workflow_dispatch"), "true", "manual")
        self.expect(self.guard(head, RC_RUN_ATTEMPT="2"), "true", "rerun")
        result = self.guard(head, RC_DRY_RUN="true", RC_BASE_OVERRIDE=self.base)
        self.expect(result, "true", "runtime:src/app.py")
        self.assertIn("(DRY-RUN)", result["summary"])
        self.assertEqual([], result["gh"])

    def test_no_base_and_api_or_jq_errors_fail_open(self) -> None:
        head = self.commit({"docs/a.md": "x"})
        one = [run(self.base, 10)]
        cases = {"no runs": {}, "runs error": {"routes": {RUNS: {"error": 1}}},
                 "runs not json": {"routes": {RUNS: {"raw": "<html>"}}},
                 "jobs error": {"runs": one},
                 "jobs page truncated": {"runs": one, "jobs_by_id": {10: {"total_count": 150, "jobs": []}}},
                 "no deploy job": {"runs": one, "jobs_by_id": {10: jobs(("test", "success"))}},
                 "dry-run, no override": {"RC_DRY_RUN": "true"}}
        for name, kwargs in cases.items():
            with self.subTest(name):
                self.expect(self.guard(head, **kwargs), "true", "fail-open:no-base")

    def test_legacy_runs_without_marker_by_event(self) -> None:
        head = self.commit({"docs/a.md": "x"})
        legacy = self.guard(head, [run(self.base, 10, marker=False)], {10: OK})
        self.expect(legacy, "true", "fail-open:legacy-marker")
        for event in ("push", "workflow_dispatch"):
            with self.subTest(event):
                result = self.guard(head, [run(self.base, 10, event, marker=False)], {10: OK})
                self.expect(result, "false", "non-runtime-only")
                self.assertEqual(self.base, result["base-sha"])

    def test_marker_beats_head_sha_of_a_workflow_run(self) -> None:
        mid = self.commit({"src/app.py": "v2"})
        head = self.commit({"docs/a.md": "x"})
        result = self.guard(head, [run(mid, 10) | {"head_sha": head}], {10: OK})
        self.expect(result, "false", "non-runtime-only")
        self.assertEqual(mid, result["base-sha"])
        self.assertIn("https://x/runs/10", result["summary"])

    def test_git_relations(self) -> None:
        newer = self.commit({"src/app.py": "v2"})
        self.expect(self.deployed(newer, newer), "false", "already-deployed")
        self.expect(self.deployed(NOPE, newer), "true", "fail-open:base-missing")
        stale = self.deployed(newer, self.base)
        self.expect(stale, "false", "stale-trigger")  # never roll production back
        self.assertIn("::notice title=deploy runtime-changes::NO-DEPLOY stale-trigger", stale["stdout"])
        self.git("switch", "-q", "-c", "side", self.base)
        side = self.commit({"docs/side.md": "x"})
        self.expect(self.deployed(side, newer), "true", "fail-open:diverged")
        self.git("switch", "-q", "main")
        reverted = self.commit({"src/app.py": "v1"})
        self.expect(self.deployed(self.base, reverted), "true", "fail-open:empty-diff")

    def test_runtime_path_deploys_and_non_runtime_only_skips(self) -> None:
        head = self.commit({"docs/a.md": "x", "src/app.py": "v2", "tests/t.py": "t"})
        result = self.deployed(self.base, head)
        self.expect(result, "true", "runtime:src/app.py")
        self.assertNotIn("NO-DEPLOY", result["stdout"])
        docs_only = self.commit({"docs/b.md": "x", "CHANGELOG.md": "x", "e2e/x.ts": "x", ".gitignore": "x",
                                 ".github/workflows/ci.yml": "x", "LICENSE": "x", ".claude/x": "x"})
        result = self.deployed(head, docs_only)
        self.expect(result, "false", "non-runtime-only")
        self.assertIn("NO-DEPLOY non-runtime-only", result["stdout"])
        self.assertIn("- non-runtime: `.github/workflows/ci.yml`", result["summary"])

    def test_force_runtime_paths_and_extras(self) -> None:
        prev = self.base
        for path in (".github/workflows/deploy.yml", ".github/actions/setup/action.yml"):
            with self.subTest(path):
                head = self.commit({path: path})
                self.expect(self.deployed(prev, head), "true", f"runtime:{path}")
                prev = head
        head = self.commit({"docs/openapi.yaml": "x"})
        forced = self.deployed(prev, head, RC_FORCE_EXTRA="# contract\n^docs/openapi\\.yaml$\n")
        self.expect(forced, "true", "runtime:docs/openapi.yaml")
        wf = self.commit({".github/workflows/deploy.yml": "v2"})
        derived = self.deployed(head, wf, RC_WORKFLOW_FILE="",
                                RC_WORKFLOW_REF="o/r/.github/workflows/deploy.yml@refs/heads/main")
        self.expect(derived, "true", "runtime:.github/workflows/deploy.yml")
        self.assertIn("workflows/deploy.yml/runs", derived["gh"][0])
        other = self.guard(wf, [run(head, 10)], {10: OK}, RC_WORKFLOW_FILE="other.yml",
                           routes={RUNS.replace("deploy.yml", "other.yml"): {"json": {"workflow_runs": [run(head, 10)]}}})
        self.expect(other, "false", "non-runtime-only")  # only the CALLING workflow is forced

    def test_deny_extra_and_invalid_regex(self) -> None:
        head = self.commit({"notebooks/x.ipynb": "x"})
        self.expect(self.deployed(self.base, head, RC_DENY_EXTRA="  ^notebooks/  \n\n"),
                    "false", "non-runtime-only")
        self.expect(self.deployed(self.base, head, RC_DENY_EXTRA="^notebooks/\n(unbalanced"),
                    "true", "fail-open:invalid-regex:(unbalanced")

    def test_rename_from_runtime_path_into_docs(self) -> None:
        head = self.commit({"src/app.py": None, "docs/app.py": "v1"})
        self.assertTrue(self.git("diff", "--name-status", "-M", self.base, head).startswith("R"))
        self.expect(self.deployed(self.base, head), "true", "runtime:src/app.py")

    def test_large_diff_with_runtime_path_last(self) -> None:
        """SIGPIPE regression: `echo "$CHANGED" | grep -q` under pipefail skipped large diffs."""
        (self.repo / "docs").mkdir()
        for i in range(4999):
            (self.repo / "docs" / f"p{i:04d}.md").write_text("x")
        result = self.deployed(self.base, self.commit({"zz/main.py": "x"}))
        self.expect(result, "true", "runtime:zz/main.py")
        self.assertIn("changed paths: 5000 (first 50 listed)", result["summary"])

    def test_paths_with_spaces_newlines_and_unicode(self) -> None:
        head = self.commit({"docs/a b.md": "x", "docs/ünï ✓.md": "x", "tests/new\nline.py": "x"})
        self.expect(self.deployed(self.base, head), "false", "non-runtime-only")
        result = self.deployed(head, self.commit({"src/new\nline.py": "x", "src/ünï.py": "x"}))
        self.expect(result, "true", "runtime:src/new\\nline.py")
        self.assertIn("`src/ünï.py`", result["summary"])

    # --- which run is the last successful deploy ------------------------------------------

    def test_matrix_leg_failure_skipped_and_cancelled_runs_are_not_deploys(self) -> None:
        older = self.commit({"src/app.py": "v2"})
        newer = self.commit({"docs/a.md": "x"})
        head = self.commit({"docs/b.md": "x"})
        result = self.guard(head, [run(newer, 40), run(newer, 30), run(head, 20), run(older, 10)], {
            40: jobs(("deploy (cz)", "success"), ("deploy (sk)", "failure")),
            30: jobs(("runtime-changes", "success"), ("deploy", "skipped")),
            20: jobs(("deploy", "cancelled")),
            10: jobs(("deploy (cz)", "success"), ("deploy (sk)", "success"))})
        self.expect(result, "false", "non-runtime-only")
        self.assertEqual(older, result["base-sha"])

    def test_current_run_excluded_and_only_ten_runs_examined(self) -> None:
        head = self.commit({"src/app.py": "v2"})
        result = self.guard(head, [run(head, 999), run(self.base, 10)], {999: OK, 10: OK})
        self.expect(result, "true", "runtime:src/app.py")
        self.assertFalse(any("runs/999/" in call for call in result["gh"]), result["gh"])
        failed = jobs(("deploy", "failure"))
        runs = [run(head, 100 - i) for i in range(10)] + [run(self.base, 5)]
        result = self.guard(head, runs, {100 - i: failed for i in range(10)} | {5: OK})
        self.expect(result, "true", "fail-open:no-base")

    # --- robustness -----------------------------------------------------------------------

    def test_bad_inputs_and_unexpected_failures_fail_open(self) -> None:
        head = self.commit({"docs/a.md": "x"})
        self.expect(self.deployed(self.base, "HEAD"), "true", "fail-open:bad-deploy-sha")
        self.expect(self.deployed(self.base, head, RC_WORKFLOW_FILE="../x;rm"),
                    "true", "fail-open:bad-workflow-file")
        (self.tmp / "file").write_text("")
        trapped = self.deployed(self.base, head, RUNNER_TEMP=str(self.tmp / "file"))  # ERR trap
        self.assertRegex(trapped["reason"], r"^fail-open:error:\d+$")
        self.expect(self.deployed(self.base, head, RUNNER_TEMP=None), "true", "fail-open:error:exit-1")


if __name__ == "__main__":
    unittest.main()
