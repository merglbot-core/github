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
                     'item["incomplete_at"] = iso()', 'iso(a) <= r["created_at"] < iso(b)', "PILOT_CENSUS_MAX = 1000",
                     "PILOT_SAMPLE_TARGET = 300", 'item.pop("partial_census", False)', "PILOT_CENSUS_VERSION = 2",
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
    """The patched #917 pilot measurement against a stub of the runs/jobs API (V6 #968, #970).

    The stub honours the `created` date-time range, `status`, `per_page` and `page` like the
    API; the fixture is the adopted function text, which the whole-function anchor replaces."""
    JOB, LABEL = "Unit tests", "ubuntu-24.04-arm"

    def setUp(self):
        import datetime as dt
        self.dt = dt
        self.since = dt.datetime(2026, 10, 1, 10, tzinfo=dt.timezone.utc)
        self.now = self.since + dt.timedelta(days=20)
        self.runs, self.jobs, self.calls = [], {}, []
        self.consts = {"MAX_CALLS_PER_TICK": 10000, "PILOT_SAMPLE_TARGET": 400}
        # three x64 runs before the merge and three arm64 runs inside the window: ratio 1.1
        for n in range(3):
            self.add(-3 - n, 500, arm=False)
            self.add(2 + n, 550, arm=True)

    def stamp(self, moment):
        return moment.strftime("%Y-%m-%dT%H:%M:%SZ")

    def add(self, day, seconds, arm, conclusion="success", hours=0, job=True, status="completed"):
        run_id = 1000 + len(self.runs)
        created = self.since + self.dt.timedelta(days=day, hours=hours)
        self.runs.append({"id": run_id, "run_attempt": 1, "status": status, "created_at": self.stamp(created)})
        entries = [{"name": "lint", "status": "completed", "completed_at": "x", "conclusion": "success",
                    "labels": ["ubuntu-24.04"], "_s": 20}]
        if job:
            entries.append({"name": self.JOB, "status": status,
                            "completed_at": "x" if status == "completed" else None, "conclusion": conclusion,
                            "labels": [self.LABEL if arm else "ubuntu-24.04"], "_s": seconds})
        self.jobs[run_id] = {"total_count": len(entries), "jobs": entries}
        return run_id

    def finish(self, run_id, seconds):
        for run in self.runs:
            if run["id"] == run_id:
                run["status"] = "completed"
        self.jobs[run_id]["jobs"][-1].update(status="completed", completed_at="x", _s=seconds)

    def gh_json(self, path):
        import re
        import urllib.parse
        self.namespace["CALLS"]["n"] += 1
        self.calls.append(path)
        route, _, query = path.partition("?")
        params = dict(urllib.parse.parse_qsl(query))
        if route.endswith("/runs") and "/workflows/" in route:
            lo, hi = params["created"].split("..")
            rows = sorted((r for r in self.runs if lo <= r["created_at"] <= hi
                           and params.get("status", r["status"]) == r["status"]),
                          key=lambda r: r["created_at"], reverse=True)
            size, page = int(params.get("per_page", 30)), int(params.get("page", 1))
            return {"total_count": len(rows), "workflow_runs": rows[(page - 1) * size:page * size]}
        match = re.search(r"/runs/(\d+)/attempts/1/jobs$", route)
        if match:
            return self.jobs[int(match.group(1))]
        run_id = int(re.search(r"/runs/(\d+)$", route).group(1))
        return next(r for r in self.runs if r["id"] == run_id)

    def measure(self, item=None, ticks=1):
        dt = self.dt
        if not hasattr(self, "function"):
            source = (ROOT / "tests/fixtures/gh_cost_adoption/pilot_adopted.py.txt").read_text()
            applied = [n for n, (before, _) in enumerate(pa.REPLACEMENTS_910) if before in source]
            self.assertEqual(len(applied), 1)
            before, after = pa.REPLACEMENTS_910[applied[0]]
            self.namespace = {
                "dt": dt, "now": lambda: self.now, "gh_json": self.gh_json, "log": lambda *_: None,
                "iso": lambda m=None: (m or self.now).astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "parse": lambda v: dt.datetime.fromisoformat(str(v).replace("Z", "+00:00")) if v else None,
                "job_seconds": lambda j: j.get("_s"), "item_since": lambda state, i: self.stamp(self.since),
                "CALLS": {"n": 0}, "PILOT_WINDOW_DAYS": 14, "PILOT_MAX_RATIO": 1.2,
                "PILOT_FAIL_TOLERANCE": 0.10, "PILOT_MIN_RUNS": 3, "PILOT_MIN_RUN_S": 60, "PILOT_CENSUS_MAX": 1000,
                "PILOT_CENSUS_VERSION": 2,
            }
            exec(compile(source.replace(before, after, 1), "pilot_adopted_patched", "exec"), self.namespace)
            self.function = self.namespace["measure_arm64_pilot"]
        self.namespace.update(self.consts)
        item = item if item is not None else {"sub": 917, "repo": "o/r", "workflow": "w.yml",
                                              "job": self.JOB, "label": self.LABEL}
        for _ in range(ticks):
            self.namespace["CALLS"]["n"] = 0
            result = self.function({}, item)
            if not item.pop("partial_census", False):
                break
        return result, item

    def test_bounded_windows_and_gate_outcomes(self):
        late = self.add(14, 5000, arm=True, hours=2)  # the window's last date, after its end time
        self.add(4, 10, arm=True, conclusion="failure")  # a V6 rejection, shorter than 60 s
        absent = self.add(5, 0, arm=True, job=False)  # a run filtered out before the job
        ok, item = self.measure()
        self.assertTrue(ok)
        self.assertEqual((item["verdict"], item["ratio_p50"], item["after"]["short"]), ("pass", 1.1, 1))
        self.assertEqual((item["after"]["n"], item["sample_rate"]), (3, 1.0))
        self.assertNotIn(str(late), item["runs"])
        self.assertEqual(item["runs"][str(absent)]["concl"], "absent")

    def test_a_running_workflow_is_listed_and_holds_the_verdict(self):
        running = self.add(6, 9000, arm=True, status="in_progress")
        ok, item = self.measure()
        self.assertFalse(any("status=" in path for path in self.calls))
        self.assertTrue(ok)
        self.assertIsNone(item["verdict"])
        self.assertEqual(item["pending_runs"], [str(running)])
        self.assertNotIn(str(running), item["runs"])
        self.finish(running, 560)
        ok, item = self.measure(item)
        self.assertEqual((item["verdict"], item["pending_runs"]), ("pass", []))

    def test_a_finished_job_in_a_running_workflow_is_not_cached(self):
        running = self.add(6, 560, arm=True)
        next(r for r in self.runs if r["id"] == running)["status"] = "in_progress"
        ok, item = self.measure()
        self.assertEqual((item["verdict"], item["pending_runs"]), (None, [str(running)]))
        self.assertNotIn(str(running), item["runs"])
        self.assertFalse(any(f"/runs/{running}/attempts/" in path for path in self.calls))
        self.finish(running, 560)
        ok, item = self.measure(item)
        self.assertEqual((item["verdict"], item["pending_runs"]), ("pass", []))
        self.assertEqual(item["runs"][str(running)]["s"], 560)

    def test_a_rerun_keeps_its_first_attempt_in_the_census(self):
        # A slow first attempt that was re-run (the re-run is still going) stays in the sample and
        # is measured by attempt 1: p95 on the label then exceeds 1.2x.
        rerun = self.add(6, 5000, arm=True)
        next(r for r in self.runs if r["id"] == rerun).update(run_attempt=2, status="in_progress")
        ok, item = self.measure()
        self.assertTrue(ok)
        self.assertEqual(item["runs"][str(rerun)]["s"], 5000)
        self.assertEqual((item["after"]["n"], item["verdict"]), (4, "regression"))

    def test_census_state_of_another_version_is_listed_again(self):
        ok, item = self.measure()
        self.assertEqual(item["verdict"], "pass")
        for entry in item["census"].values():
            entry["v"], entry["runs"] = 1, {}
        item.update(sample_rate_v=1, verdict=None)
        item.pop("met_at")
        self.calls.clear()
        ok, item = self.measure(item)
        self.assertEqual((item["verdict"], item["sample_rate_v"], item["after"]["n"]), ("pass", 2, 3))
        self.assertTrue(any("/workflows/" in path for path in self.calls))

    def test_runs_cached_by_the_adopted_code_are_measured_again(self):
        # The adopted code cached a fast measurement for a run the new rules read as slow.
        old = self.add(6, 5000, arm=True)
        item = {"sub": 917, "repo": "o/r", "workflow": "w.yml", "job": self.JOB, "label": self.LABEL,
                "runs": {str(old): {"created": "x", "labels": [self.LABEL], "s": 550, "concl": "success"}}}
        ok, item = self.measure(item)
        self.assertEqual(item["runs"][str(old)]["s"], 5000)
        self.assertEqual(item["verdict"], "regression")
        self.assertNotIn("incomplete_at", item)

    def test_every_page_of_a_busy_day_is_counted(self):
        # 130 slow arm64 runs on one day: they only fit on two pages of 100.
        for n in range(130):
            self.add(7, 900, arm=True, hours=n % 10)
        ok, item = self.measure()
        self.assertTrue(ok)
        self.assertEqual((item["after"]["n"], item["verdict"]), (133, "regression"))
        self.assertTrue(any("page=2" in path for path in self.calls))

    def test_the_sample_is_uniform_deterministic_and_resumes_across_ticks(self):
        for n in range(400):
            self.add(-1 - n % 13, 500, arm=False, hours=n % 20)
            self.add(1 + n % 13, 520, arm=True, hours=n % 20)
        self.consts.update(PILOT_SAMPLE_TARGET=100, MAX_CALLS_PER_TICK=50)
        ok, item = self.measure(ticks=100)
        self.assertEqual(item["verdict"], "pass")
        chosen, population = item["sample"]["before"]
        self.assertEqual(population, 403)
        self.assertLess(abs(chosen - 100), 30)
        rate, runs = item["sample_rate"], dict(item["runs"])
        ok, again = self.measure(dict(item, runs={}, census={}, verdict=None, sample_rate=None), ticks=100)
        self.assertEqual((again["sample_rate"], sorted(again["runs"])), (rate, sorted(runs)))

    def test_a_failed_or_partial_read_decides_nothing(self):
        broken = self.add(6, 550, arm=True)
        for listing in (None, {"jobs": []}, {"total_count": 101, "jobs": self.jobs[broken]["jobs"]}):
            self.jobs[broken] = listing
            ok, item = self.measure()
            self.assertFalse(ok)
            self.assertIn("incomplete_at", item)
            self.assertNotIn("verdict", item)
            self.assertNotIn(str(broken), item["runs"])

    def test_a_day_the_api_cannot_list_whole_decides_nothing(self):
        for n in range(3):
            self.add(8, 550, arm=True)
        ok, item = self.measure()
        self.assertEqual(item["verdict"], "pass")
        self.namespace["PILOT_CENSUS_MAX"] = 2
        ok, item = self.measure()
        self.assertFalse(ok)
        self.assertNotIn("verdict", item)

if __name__ == "__main__":
    unittest.main()
