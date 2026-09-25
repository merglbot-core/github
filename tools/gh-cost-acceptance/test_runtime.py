"""Integration-shaped tests of the patched DoD function without GitHub calls."""

import ast
import base64
import importlib.util
import pathlib
import sys
import unittest

HERE = pathlib.Path(__file__).parent
spec = importlib.util.spec_from_file_location("cost_patch", HERE / "patch_autopilot.py")
patcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(patcher)
sys.path.insert(0, str(HERE))
semantic_spec = importlib.util.spec_from_file_location("cost_semantic", HERE / "semantic.py")
semantic = importlib.util.module_from_spec(semantic_spec)
semantic_spec.loader.exec_module(semantic)
sys.modules["cost_semantic"] = semantic


def patched_function():
    code = patcher.NEW_NO_PUSH
    tree = ast.parse(code)
    return compile(tree, "reviewed_measurer", "exec")


class CurrentMainBinding(unittest.TestCase):
    def evaluate(self, *, second_sha="current", second_total=1):
        branch_reads = []
        calls = []
        old_run = {"id": 35921187304, "event": "push", "head_sha": "parent",
                   "created_at": "2026-09-23T21:14:29Z", "conclusion": "success"}
        workflow = "on:\n  pull_request:\n    branches: [main]\n  schedule:\n    - cron: '15 3 * * 1'\n"
        def api(path):
            calls.append(path)
            if path.endswith("/branches/main"):
                branch_reads.append(path)
                return {"commit": {"sha": "current" if len(branch_reads) == 1 else second_sha}}
            if "/contents/" in path:
                self.assertTrue(path.endswith("?ref=current"))
                return {"content": base64.b64encode(workflow.encode()).decode()}
            if "/actions/workflows/" in path:
                if path.endswith("&page=1"):
                    return {"total_count": 1 if second_total == 1 else 2, "workflow_runs": [old_run]}
                return {"total_count": second_total, "workflow_runs": []}
            if path.endswith("/commits/merge"):
                return {"parents": [{"sha": "parent"}]}
            if "/commits?" in path:
                self.assertIn("sha=current", path)
                return [{"sha": "current"}]
            self.fail(path)
        namespace = {"gh_json": api, "item_since": lambda *_: "2026-09-23T21:05:07Z",
                     "CALLS": {"n": 0}, "MAX_CALLS_PER_TICK": 60,
                     "iso": lambda: "2026-09-25T12:00:00Z", "log": lambda *_: None}
        exec(patched_function(), namespace)
        item = {"sub": 915, "repo": "merglbot-core/project-management-app", "workflow": "ci.yml",
                "since_pr": "merglbot-core/project-management-app#332"}
        state = {"prs": {item["since_pr"]: {"merge_sha": "merge"}}}
        answer = namespace["measure_no_push_runs"](state, item)
        return answer, item, calls

    def test_delayed_parent_does_not_create_false_push(self):
        answer, item, calls = self.evaluate()
        self.assertTrue(answer)
        self.assertEqual(item["push_runs"], 0)
        self.assertEqual(item["excluded_premerge_run_ids"], [35921187304])
        self.assertIn("met_at", item)
        self.assertEqual(sum(call.endswith("/branches/main") for call in calls), 2)

    def test_main_moves_before_acceptance(self):
        answer, item, _ = self.evaluate(second_sha="newer")
        self.assertFalse(answer)
        self.assertNotIn("met_at", item)

    def test_changing_page_total_never_proves_zero(self):
        answer, item, _ = self.evaluate(second_total=0)
        self.assertFalse(answer)
        self.assertFalse(item["pagination_complete"])
        self.assertNotIn("met_at", item)


if __name__ == "__main__":
    unittest.main()
