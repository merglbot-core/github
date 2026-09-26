import ast
import copy
import importlib.util
import json
import math
from pathlib import Path
import tempfile
import unittest

HERE = Path(__file__).parent
spec = importlib.util.spec_from_file_location("patch888", HERE / "patch_autopilot.py")
patcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(patcher)


class RuntimeTests(unittest.TestCase):
    def evaluate(self, patched=True, override=None, repeats=1, before=None):
        source = (HERE / "fixture_post_billing.py").read_text()
        if patched:
            source = patcher.patch(source)
            self.assertEqual(patcher.patch(source), source)
        state = {"prs": {"org/repo#1": {"merged_at": "2026-09-23T00:00:00Z"}}, "subs": {},
                 "billing": {"before_days": ["2026-09-09"], "after_days": ["2026-09-27"]}}
        row = dict(product="actions", sku="Actions Linux", grossAmount=200.0, quantity=33333.0,
                   organizationName="org", repositoryName="repo", date="2026-09-09")
        rows = [{**row, **(before or {})}] * repeats + [{**row, "date": "2026-09-27", "quantity": 0, **(override or {})}]
        calls = []
        with tempfile.TemporaryDirectory() as directory:
            scope = dict(json=json, BILLING_DIR=Path(directory), BILLING_WINDOW_DAYS=14,
                         ACTIONS_USD_PER_MINUTE=0.006, BILLING_SUB=896, EPIC_REPO="org/program",
                         STATUS_DONE="done", DRY_RUN=False, gh_json=lambda path: {"usageItems": copy.deepcopy(rows)},
                         log=lambda *args: None, iso=lambda: "sandbox", save_state=lambda state: calls.append("save"),
                         gh=lambda *args: calls.append("close"), comment=lambda *args: calls.append("comment") or True,
                         board=lambda *args: calls.append("board"), notify=lambda *args: None)
            exec(compile(source, "fixture888.py", "exec"), scope)
            result = scope["post_billing"](state, state["billing"])
            if patched and not result:
                self.assertFalse((Path(directory) / "acceptance.json").exists())
        return result, state, calls

    def test_actual_null_counterexample_and_fix(self):
        result, state, calls = self.evaluate(False, {"quantity": None})
        self.assertTrue(result)
        self.assertEqual(state["billing"]["verdict"], "FAKT")
        self.assertIn("close", calls)
        result, state, calls = self.evaluate(True, {"quantity": None})
        self.assertFalse(result)
        self.assertEqual(calls, [])
        self.assertNotIn("posted_at", state["billing"])

    def test_invalid_or_overflowing_data_never_publish(self):
        for override, before, repeats in (({"quantity": False}, None, 1),
                                         ({"quantity": math.inf}, None, 1),
                                         ({"date": "2026-09-27junk"}, None, 1),
                                         ({"date": "2026-10-01"}, None, 1),
                                         ({}, {"quantity": 1e308}, 1),
                                         ({}, {"quantity": 1e308}, 2)):
            with self.subTest(override=override, before=before, repeats=repeats):
                result, state, calls = self.evaluate(True, override, repeats, before)
                self.assertFalse(result)
                self.assertEqual(calls, [])

    def test_genuine_zero_retains_existing_minute_model(self):
        result, state, calls = self.evaluate()
        self.assertTrue(result)
        self.assertEqual(state["billing"]["saved_usd_month"], 428.6)
        self.assertIn("close", calls)

    def test_incomplete_guard_or_source_drift_rejected(self):
        source = patcher.patch((HERE / "fixture_post_billing.py").read_text())
        node = next(n for n in ast.walk(ast.parse(source)) if isinstance(n, ast.If)
                    and any(isinstance(v, ast.Constant) and v.value == "billing accumulation overflow; acceptance blocked"
                            for v in ast.walk(n)))
        lines = source.splitlines(keepends=True)
        del lines[node.lineno - 1:node.end_lineno]
        with self.assertRaisesRegex(ValueError, "incomplete"):
            patcher.patch("".join(lines))
        with self.assertRaisesRegex(ValueError, "source drift"):
            patcher.patch((HERE / "fixture_post_billing.py").read_text().replace('item.get("quantity") or 0', '0'))


if __name__ == "__main__":
    unittest.main()
