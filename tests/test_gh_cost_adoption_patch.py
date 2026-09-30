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

    def test_patched_source_with_a_legacy_or_duplicated_block_is_refused(self):
        for name, replacements, patch in self.CASES:
            with self.subTest(name):
                patched = patch(fixture(replacements))
                before, after = replacements[1]
                with self.assertRaisesRegex(ValueError, "legacy block"):
                    patch(patched + before)
                with self.assertRaisesRegex(ValueError, "duplicated patched block"):
                    patch(patched + after)

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
        for text in ("live_caller_config(gh_json, repo, workflow, state.get(\"prs\", {}).get(item.get(\"since_pr\"))", "PILOT_MIN_RUN_S = 60",
                     'item["incomplete_at"] = iso()', "lo <= r[\"created\"] < since", "since <= r[\"created\"] < hi",
                     'item["verdict"] = "regression" if slower else "pass"', "fail_verdict",
                     "billing_retry_blocked", "closes_billing(verdict)"):
            self.assertIn(text, p910)
        self.assertNotIn('verdict = "FAKT" if saved_usd >= 0.8 * model else "DATA_GAP"', p888 + p910)

    def test_every_cost_adoption_import_exists(self):
        # The patched autopilots import these names lazily; a missing one would only fail at
        # runtime inside a sweep (V6 #970). cost_adoption.py lands with #969.
        import re
        helpers = load("cost_adoption_contract", "tools/gh-cost-autopilot-adoption/cost_adoption.py")
        names = set()
        for _, after in pa.REPLACEMENTS_888 + pa.REPLACEMENTS_910:
            for match in re.finditer(r"from cost_adoption import ([\w, ]+)", after):
                names |= {name.strip() for name in match.group(1).split(",")}
        self.assertTrue(names)
        self.assertEqual(sorted(n for n in names if not hasattr(helpers, n)), [])

    def test_module_parses_as_python_3_9(self):
        source = (ROOT / "tools/gh-cost-autopilot-adoption/patch_autopilot.py").read_text()
        ast.parse(source, feature_version=(3, 9))


class PilotBehaviour(unittest.TestCase):
    """The patched #917 pilot measurement run against stubbed GitHub reads (V6 #968, #970).

    The fixture is the adopted function text; only the pilot anchors apply to it."""
    SINCE = "2026-10-01T10:00:00Z"
    JOB, LABEL = "Unit tests", "ubuntu-24.04-arm"

    def setUp(self):
        import datetime as dt
        self.dt = dt
        self.since = dt.datetime(2026, 10, 1, 10, tzinfo=dt.timezone.utc)
        self.now = self.since + dt.timedelta(days=20)
        self.runs, self.jobs = [], {}
        # three x64 runs before the merge and three arm64 runs inside the window: ratio 1.1
        for n in range(3):
            self.add(-3 - n, 500, arm=False)
            self.add(2 + n, 550, arm=True)

    def add(self, day, seconds, arm, conclusion="success", hours=0, job=True, status="completed"):
        run_id = len(self.runs) + 1
        created = self.since + self.dt.timedelta(days=day, hours=hours)
        self.runs.append({"id": run_id, "run_attempt": 1, "status": "completed",
                          "created_at": created.strftime("%Y-%m-%dT%H:%M:%SZ")})
        entries = [{"name": "lint", "status": "completed", "completed_at": "x", "conclusion": "success",
                    "labels": ["ubuntu-24.04"], "_s": 20}]
        if job:
            entries.append({"name": self.JOB, "status": status,
                            "completed_at": "x" if status == "completed" else None, "conclusion": conclusion,
                            "labels": [self.LABEL if arm else "ubuntu-24.04"], "_s": seconds})
        self.jobs[run_id] = {"total_count": len(entries), "jobs": entries}
        return run_id

    def gh_json(self, path):
        import re
        match = re.search(r"/runs\?created=([0-9-]+)\.\.([0-9-]+)&", path)
        if match:
            lo, hi = match.groups()
            return {"workflow_runs": [r for r in self.runs if lo <= r["created_at"][:10] <= hi]}
        run_id = int(re.search(r"/runs/(\d+)/jobs", path).group(1))
        return self.jobs[run_id]

    def measure(self):
        dt = self.dt
        source = (ROOT / "tests/fixtures/gh_cost_adoption/pilot_adopted.py.txt").read_text()
        applied = 0
        for before, after in pa.REPLACEMENTS_910:
            if before in source:
                source, applied = source.replace(before, after, 1), applied + 1
        self.assertEqual(applied, 5)
        namespace = {
            "dt": dt, "now": lambda: self.now, "gh_json": self.gh_json, "log": lambda *_: None,
            "iso": lambda m=None: (m or self.now).astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "parse": lambda v: dt.datetime.fromisoformat(str(v).replace("Z", "+00:00")) if v else None,
            "job_seconds": lambda j: j.get("_s"), "item_since": lambda state, item: self.SINCE,
            "CALLS": {"n": 0}, "MAX_CALLS_PER_TICK": 1000, "PILOT_WINDOW_DAYS": 14, "PILOT_MAX_RATIO": 1.2,
            "PILOT_FAIL_TOLERANCE": 0.10, "PILOT_MIN_RUNS": 3, "PILOT_SAMPLE": 30, "PILOT_MIN_RUN_S": 60,
        }
        exec(compile(source, "pilot_adopted_patched", "exec"), namespace)
        item = {"sub": 917, "repo": "o/r", "workflow": "w.yml", "job": self.JOB, "label": self.LABEL}
        return namespace["measure_arm64_pilot"]({}, item), item

    def test_bounded_windows_and_gate_outcomes(self):
        late = self.add(14, 5000, arm=True, hours=2)  # the window's last date, after its end time
        self.add(4, 10, arm=True, conclusion="failure")  # a V6 rejection, shorter than 60 s
        absent = self.add(5, 0, arm=True, job=False)  # a run filtered out before the job
        ok, item = self.measure()
        self.assertTrue(ok)
        self.assertEqual((item["verdict"], item["ratio_p50"], item["after"]["short"]), ("pass", 1.1, 1))
        self.assertEqual(item["after"]["n"], 3)
        self.assertIn(str(late), item["runs"])
        self.assertEqual(item["runs"][str(absent)]["concl"], "absent")

    def test_an_unfinished_job_is_never_cached_and_holds_the_verdict(self):
        running = self.add(6, 9000, arm=True, status="in_progress")
        ok, item = self.measure()
        self.assertTrue(ok)
        self.assertIsNone(item["verdict"])
        self.assertEqual(item["pending_runs"], [str(running)])
        self.assertNotIn(str(running), item["runs"])
        self.jobs[running]["jobs"][-1].update(status="completed", completed_at="x", _s=560)
        ok, item = self.measure()
        self.assertEqual((item["verdict"], item["pending_runs"]), ("pass", []))

    def test_a_failed_or_partial_jobs_read_decides_nothing(self):
        broken = self.add(6, 550, arm=True)
        for listing in (None, {"jobs": []}, {"total_count": 101, "jobs": self.jobs[broken]["jobs"]}):
            self.jobs[broken] = listing
            ok, item = self.measure()
            self.assertFalse(ok)
            self.assertIn("incomplete_at", item)
            self.assertNotIn("verdict", item)
            self.assertNotIn(str(broken), item["runs"])


if __name__ == "__main__":
    unittest.main()
