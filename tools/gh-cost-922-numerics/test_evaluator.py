import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

HERE = Path(__file__).parent
spec = importlib.util.spec_from_file_location("numeric_patch", HERE / "patch_autopilot.py")
patcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(patcher)


class EvaluatorTests(unittest.TestCase):
    def run_evaluator(self, patched, after, before=None, repeats=1):
        source = (HERE / "fixture_post_billing.py").read_text()
        if patched:
            source = patcher.patch(source)
            self.assertEqual(patcher.patch(source), source)
        state = {"prs": {"org/repo#1": {"merged_at": "2026-09-23T00:00:00Z"}}, "subs": {},
                 "billing": {"before_days": ["2026-09-09"], "after_days": ["2026-09-27"]}}
        calls = []
        row = dict(product="actions", sku="Actions Linux", organizationName="org",
                   repositoryName="repo", date="2026-09-09", grossAmount=200.0, quantity=33333.0)
        data = {"usageItems": [{**row, **(before or {})}] * repeats + [{**row, "date": "2026-09-27", **after}]}
        with tempfile.TemporaryDirectory() as tmp:
            scope = dict(json=json, BILLING_DIR=Path(tmp), BILLING_WINDOW_DAYS=14,
                         BILLING_SUB=922, EPIC_REPO="org/program", STATUS_DONE="done", DRY_RUN=False,
                         billing_inputs=lambda state: [(None, "org/repo#1")],
                         gh_json=lambda path: copy.deepcopy(data), log=lambda *args: None,
                         iso=lambda: "sandbox", save_state=lambda state: calls.append("save"),
                         gh=lambda *args: calls.append("close") or (0, "", ""),
                         comment=lambda *args: calls.append("comment") or True,
                         board=lambda *args: calls.append("board"), notify=lambda *args: None)
            exec(compile(source, "fixture_post_billing.py", "exec"), scope)
            result = scope["post_billing"](state, state["billing"])
            if patched and not result:
                self.assertFalse((Path(tmp) / "acceptance.json").exists())
        return result, state, calls

    def test_existing_null_counterexample_closes_but_patch_blocks(self):
        result, state, calls = self.run_evaluator(False, {"grossAmount": None, "quantity": None})
        self.assertTrue(result)
        self.assertEqual(state["billing"]["verdict"], "FAKT")
        self.assertIn("close", calls)

    def test_finite_inputs_with_overflow_cannot_write_or_close(self):
        for before, repeats in (({"grossAmount": 1e308}, 1),
                                ({"grossAmount": 1e308}, 2),
                                ({"quantity": 1e308}, 2)):
            with self.subTest(before=before, repeats=repeats):
                result, state, calls = self.run_evaluator(True, {"grossAmount": 0, "quantity": 0}, before, repeats)
                self.assertFalse(result)
                self.assertNotIn("posted_at", state["billing"])
                self.assertEqual(calls, [])
        result, state, calls = self.run_evaluator(True, {"grossAmount": None, "quantity": None})
        self.assertFalse(result)
        self.assertNotIn("posted_at", state["billing"])
        self.assertEqual(calls, [])

    def test_real_numeric_zero_preserves_existing_gross_behavior(self):
        result, state, calls = self.run_evaluator(True, {"grossAmount": 0.0, "quantity": 0})
        self.assertTrue(result)
        self.assertEqual(state["billing"]["verdict"], "FAKT")
        self.assertIn("close", calls)


if __name__ == "__main__":
    unittest.main()
