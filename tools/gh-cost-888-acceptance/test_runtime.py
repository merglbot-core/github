"""Exercise patched #892 verifier with bounded, complete API fixtures."""

import ast
import base64
import importlib.util
import pathlib
import sys
import unittest

HERE = pathlib.Path(__file__).parent
module_spec = importlib.util.spec_from_file_location("cost_888_literal", HERE / "literal.py")
literal = importlib.util.module_from_spec(module_spec)
module_spec.loader.exec_module(literal)
sys.modules["cost_888_literal"] = literal
patch_spec = importlib.util.spec_from_file_location("patch_888", HERE / "patch_autopilot.py")
patcher = importlib.util.module_from_spec(patch_spec)
patch_spec.loader.exec_module(patcher)
SINCE = "2026-09-23T11:00:00Z"


class PatchedVerifier(unittest.TestCase):
    def run_measure(self, successful, *, page_total=None, main_end="head", pull_count=8):
        workflow = "on:\n  pull_request:\n    branches: [main]\n    paths:\n      - 'terraform/**'\n"
        rows = [{"id": index + 1, "event": "pull_request", "created_at": "2026-09-24T12:00:00Z",
                 "run_attempt": 1, "conclusion": "success"} for index in range(successful)]
        total = successful if page_total is None else page_total
        pulls = [{"number": index + 1, "created_at": "2026-09-24T11:00:00Z"}
                 for index in range(pull_count)]
        seen = []
        def api(path):
            seen.append(path)
            if path.endswith("/branches/main"):
                return {"commit": {"sha": "head" if len([x for x in seen if x.endswith("/branches/main")]) == 1 else main_end}}
            if "/contents/" in path:
                self.assertTrue(path.endswith("?ref=head"))
                return {"content": base64.b64encode(workflow.encode()).decode()}
            if "/actions/workflows/" in path:
                return {"total_count": total, "workflow_runs": rows}
            if "/actions/runs/" in path and path.endswith("/jobs?per_page=100"):
                return {"total_count": 1, "jobs": [{"conclusion": "success",
                        "started_at": "2026-09-24T12:00:00Z", "completed_at": "2026-09-24T12:00:42Z"}]}
            if "/pulls?" in path:
                return pulls
            self.fail(path)
        code = compile(ast.parse(patcher.NEW_FILTERED), "patched_filtered", "exec")
        scope = {"gh_json": api, "merged_at": lambda *_: SINCE,
                 "CALLS": {"n": 0}, "MAX_CALLS_PER_TICK": 60,
                 "iso": lambda: "2026-09-25T12:00:00Z", "DOD_MAX_SECONDS": 60}
        exec(code, scope)
        item = {"sub": 892, "repo": "merglbot-core/merglbot-admin",
                "workflow": "terraform-validate.yml", "since_pr": "merglbot-core/merglbot-admin#1039",
                "met_at": "historical_proxy"}
        result = scope["measure_filtered"]({}, item)
        return result, item

    def test_one_natural_run_clears_proxy(self):
        ok, item = self.run_measure(1)
        self.assertTrue(ok)
        self.assertEqual(item["runs_after"], 1)
        self.assertEqual(item["qualified_runs"], 1)
        self.assertFalse(item["literal_verified"])
        self.assertIsNone(item["met_at"])

    def test_five_natural_runs_meet_literal_criterion(self):
        ok, item = self.run_measure(5)
        self.assertTrue(ok)
        self.assertTrue(item["literal_verified"])
        self.assertEqual(item["runs_after"], 5)
        self.assertEqual(item["qualified_runs"], 5)

    def test_six_runs_for_six_prs_are_not_reduced_execution(self):
        ok, item = self.run_measure(6, pull_count=6)
        self.assertTrue(ok)
        self.assertEqual(item["qualified_runs"], 5)
        self.assertEqual(item["runs_after"], 6)
        self.assertFalse(item["literal_verified"])
        self.assertIsNone(item["met_at"])

    def test_incomplete_page_or_moving_main_cannot_accept(self):
        self.assertFalse(self.run_measure(5, page_total=6)[0])
        self.assertFalse(self.run_measure(5, main_end="moved")[0])


if __name__ == "__main__":
    unittest.main()
