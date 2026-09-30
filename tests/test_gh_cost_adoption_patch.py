"""Tests for tools/gh-cost-autopilot-adoption/patch_autopilot.py (owner plan of 30 Sep 2026).

The live autopilots are not in this repository, so the fixtures are the anchor texts
themselves; the installer additionally re-checks the real files before writing."""
import ast
import importlib.util
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


pa = load("adoption_patch", "tools/gh-cost-autopilot-adoption/patch_autopilot.py")
closeout = load("closeout_patch", "tools/gh-cost-910-closeout/patch_autopilot.py")


def fixture(replacements):
    return "".join(f"# segment {n}\n{before}\n" for n, (before, _) in enumerate(replacements))


class Mechanics(unittest.TestCase):
    CASES = (("888", pa.REPLACEMENTS_888, pa.patch_888), ("910", pa.REPLACEMENTS_910, pa.patch_910))

    def test_every_anchor_is_replaced_once_and_repatching_is_a_no_op(self):
        for name, replacements, patch in self.CASES:
            with self.subTest(name):
                source = fixture(replacements)
                patched = patch(source)
                for before, after in replacements:
                    self.assertIn(after, patched)
                self.assertEqual(patch(patched), patched)

    def test_missing_or_duplicated_anchor_fails_closed(self):
        for name, replacements, patch in self.CASES:
            with self.subTest(name):
                source = fixture(replacements)
                first = replacements[0][0]
                with self.assertRaisesRegex(ValueError, "anchor drift"):
                    patch(source.replace(first, ""))
                with self.assertRaisesRegex(ValueError, "anchor drift"):
                    patch(source + first)

    def test_partially_patched_source_is_refused(self):
        for name, replacements, patch in self.CASES:
            with self.subTest(name):
                before, after = replacements[-1]
                with self.assertRaisesRegex(ValueError, "partially patched"):
                    patch(fixture(replacements).replace(before, after))


class Content(unittest.TestCase):
    def test_910_close_key_no_longer_offers_the_closeout_anchor(self):
        patched = pa.patch_910(fixture(pa.REPLACEMENTS_910))
        self.assertNotIn('f"dod:{sub}"', patched)
        self.assertIn('key = "dod:" + str(sub)', patched)
        banner = "\n\n# --------------------------------------------------------------------------- billing"
        legacy_close = ('def close_finished_subs(state):\n        record["board_done_at"] = iso()\n'
                        '        key = "dod:" + str(sub)' + banner)
        with self.assertRaisesRegex(ValueError, "close_finished_subs drift"):
            closeout.patch(legacy_close)

    def test_owner_decisions_reach_the_patched_code(self):
        p888 = pa.patch_888(fixture(pa.REPLACEMENTS_888))
        p910 = pa.patch_910(fixture(pa.REPLACEMENTS_910))
        for text in ("live_caller_config", "owner_exception_live_ok", "billing_verdict",
                     "closes_billing(verdict)", "live_children_done", "dod_sweep_started_at",
                     'f"dod:{sub}:owner-exception"'):
            self.assertIn(text, p888)
        for text in ("live_caller_config(gh_json, repo, workflow, None, label=label)", "PILOT_MIN_RUN_S = 60",
                     'item["verdict"] = "regression" if slower else "pass"', "fail_verdict",
                     "billing_retry_blocked", "closes_billing(verdict)"):
            self.assertIn(text, p910)
        self.assertNotIn('verdict = "FAKT" if saved_usd >= 0.8 * model else "DATA_GAP"', p888 + p910)

    def test_module_parses_as_python_3_9(self):
        source = (ROOT / "tools/gh-cost-autopilot-adoption/patch_autopilot.py").read_text()
        ast.parse(source, feature_version=(3, 9))


if __name__ == "__main__":
    unittest.main()
