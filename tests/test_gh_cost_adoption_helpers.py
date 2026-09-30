"""Tests for tools/gh-cost-autopilot-adoption/cost_adoption.py (owner plan of 30 Sep 2026)."""
import base64
import datetime as dt
import importlib.util
import json
import pathlib
import sys
import types
import unittest

try:
    import yaml  # noqa: F401  (the real parser; it ships with the launchd Python)
except ImportError:
    # Hub CI installs no PyYAML before unittest discovery. Every workflow fixture below is JSON,
    # which is valid YAML, so a JSON-backed stand-in drives the same code paths there instead
    # of skipping them (V6 #969). With PyYAML present the real parser reads the same fixtures.
    _shim = types.ModuleType("yaml")
    _shim.safe_load, _shim.YAMLError = json.loads, ValueError
    sys.modules["yaml"] = _shim

ROOT = pathlib.Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "cost_adoption", ROOT / "tools/gh-cost-autopilot-adoption/cost_adoption.py")
ca = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ca)

MAIN, MOVED, HUB_SHA = "a" * 40, "b" * 40, "c" * 40
REPO = "o/r"
PIN = f"merglbot-core/github/.github/workflows/pr-gate.yml@{HUB_SHA}"


def content(text):
    return {"encoding": "base64", "content": base64.b64encode(text.encode()).decode()}


class Seq(list):
    """Answers consumed in order (to model a moving branch)."""


class FakeGH:
    """Path -> value; a Seq value is consumed in order."""
    def __init__(self, routes):
        self.routes, self.calls = dict(routes), []

    def __call__(self, path):
        self.calls.append(path)
        value = self.routes.get(path)
        if isinstance(value, Seq):
            return value.pop(0) if value else None
        return value


PR_NUMBER = {"pull-request-number": "${{ github.event.pull_request.number }}"}


def caller(job=None, on="pull_request", jobs=None, uses=PIN, env=None):
    job = dict(job or {})
    job["with"] = dict(PR_NUMBER, **job.get("with", {})) if "with" not in job or isinstance(job["with"], dict) else job["with"]
    gate = dict({"uses": uses}, **job)
    doc = {"name": "PR Gate", "on": on, "jobs": dict({"pr-gate": gate}, **(jobs or {}))}
    if env:
        doc["env"] = env
    return json.dumps(doc)


def hub(runs_on, default="ubuntu-24.04"):
    return json.dumps({"on": {"workflow_call": {"inputs": {"runs-on": {"type": "string", "default": default},
                                                           "mode": {"default": "advisory"},
                                                           "pull-request-number": {"type": "number", "required": True}}}},
                       "jobs": {"pr-gate": {"runs-on": runs_on}}})


# The pinned hub at c17b925b: allowlist expression, default ubuntu-24.04.
HUB_ALLOWLIST = hub("${{ inputs.runs-on == 'ubuntu-slim' && 'ubuntu-slim' || 'ubuntu-24.04' }}")
HUB_DEFAULT_SLIM = hub("${{ inputs.runs-on }}", default="ubuntu-slim")


def routes(caller_text, listing=None, mains=None, hub_text=None):
    base = {
        f"repos/{REPO}/branches/main": Seq(mains or [{"commit": {"sha": MAIN}}, {"commit": {"sha": MAIN}}]),
        f"repos/{REPO}/contents/.github/workflows?ref={MAIN}": listing if listing is not None else
        [{"path": ".github/workflows/pr-gate.yml"}, {"path": ".github/workflows/ci.yml"}],
        f"repos/{REPO}/contents/.github/workflows/pr-gate.yml?ref={MAIN}": content(caller_text),
    }
    text = hub_text if hub_text is not None else HUB_ALLOWLIST
    if text:
        base[f"repos/merglbot-core/github/contents/.github/workflows/pr-gate.yml?ref={HUB_SHA}"] = content(text)
    return base


class Basics(unittest.TestCase):
    def test_parse_utc_accepts_z_and_rejects_naive(self):
        self.assertEqual(ca.parse_utc("2026-10-01T10:00:00Z").hour, 10)
        with self.assertRaises(ValueError):
            ca.parse_utc("2026-10-01T10:00:00")

    def test_billing_verdict_boundaries(self):
        self.assertEqual(ca.billing_verdict(320, 400), "FAKT")
        self.assertEqual(ca.billing_verdict(319.99, 400), "PARTIAL")
        self.assertEqual(ca.billing_verdict(0, 400), "BEZ ÚSPORY")
        self.assertEqual(ca.billing_verdict(-5, 400), "BEZ ÚSPORY")
        self.assertEqual(ca.billing_verdict(0, 0), "DATA_GAP")  # a non-positive model never closes (V6 #969)
        for value in (float("nan"), float("inf"), True, 10 ** 400, "400", None):
            self.assertEqual(ca.billing_verdict(value, 400), "DATA_GAP")
            self.assertFalse(ca.closes_billing(ca.billing_verdict(400, value)))
        self.assertEqual(ca.billing_verdict(True, 1), "DATA_GAP")
        self.assertEqual(ca.billing_verdict(5, 0), "DATA_GAP")
        self.assertTrue(ca.closes_billing("PARTIAL"))
        self.assertFalse(ca.closes_billing("BEZ ÚSPORY"))
        self.assertIn("30. 9. 2026", ca.verdict_note("PARTIAL"))

    def test_retry_and_data_gap_timing(self):
        now = ca.parse_utc("2026-10-09T08:00:00Z")
        self.assertTrue(ca.billing_retry_blocked({"retry_after": "2026-10-09T08:30:00Z"}, now))
        self.assertFalse(ca.billing_retry_blocked({"retry_after": "2026-10-09T07:59:00Z"}, now))
        self.assertFalse(ca.billing_retry_blocked({}, now))
        self.assertFalse(ca.billing_retry_blocked({"retry_after": "not a time"}, now))
        self.assertFalse(ca.billing_data_gap_due("2026-10-06T06:00:00", now))  # no timezone
        self.assertFalse(ca.billing_data_gap_due("2026-10-09T06:00:00Z", now))
        self.assertTrue(ca.billing_data_gap_due("2026-10-06T06:00:00Z", now))



class Structure(unittest.TestCase):
    def test_only_job_level_calls_count_with_any_ref_and_casing(self):
        text = caller(env={"NOTE": f"uses: {PIN}"}, jobs={
            "other": {"runs-on": "ubuntu-24.04", "steps": [{"run": f"uses: {PIN}"}]},
            "branch": {"uses": "merglbot-core/github/.github/workflows/pr-gate.yml@main"},
            "short": {"uses": "merglbot-core/github/.github/workflows/pr-gate.yml@c17b925"},
            "cased": {"uses": f"Merglbot-Core/GitHub/.github/workflows/pr-gate.yml@{HUB_SHA}"}})
        doc, reason = ca.parse_workflow(text)
        self.assertEqual([(j, ref) for j, ref, _ in ca.hub_jobs(doc)],
                         [("pr-gate", HUB_SHA), ("branch", "main"), ("short", "c17b925"), ("cased", HUB_SHA)], reason)

    def test_hub_input_default(self):
        self.assertEqual(ca.hub_input_default(HUB_DEFAULT_SLIM, "runs-on"), "ubuntu-slim")
        self.assertEqual(ca.hub_input_default(HUB_DEFAULT_SLIM, "mode"), "advisory")
        self.assertIsNone(ca.hub_input_default(HUB_DEFAULT_SLIM, "absent"))
        self.assertIsNone(ca.hub_input_default(hub("x", default="${{ vars.RUNNER }}"), "runs-on"))

    def test_bare_on_key_read_as_true_still_counts(self):
        self.assertTrue(ca.runs_on_pull_requests({True: "pull_request"}, {}))

    def test_unparseable_or_jobless_workflow_fails(self):
        self.assertIsNone(ca.parse_workflow("jobs: [unclosed")[0])
        self.assertIsNone(ca.parse_workflow(json.dumps({"name": "x"}))[0])


class LiveCallerConfig(unittest.TestCase):
    SLIM = {"with": {"runs-on": "ubuntu-slim"}}
    RECORD = {"expected_files": [".github/workflows/pr-gate.yml"], "deleted_files": []}

    def run_check(self, gh, **kw):
        kw.setdefault("pr_record", self.RECORD)
        return ca.live_caller_config(gh, REPO, "pr-gate.yml", **kw)

    def test_rollout_record_is_required(self):
        for record in (None, {}, {"expected_files": [".github/workflows/pr-gate.yml"]}, {"expected_files": "x", "deleted_files": []}):
            result = self.run_check(FakeGH(routes(caller())), pr_record=record)
            self.assertFalse(result["ok"], record)
            self.assertIn("rollout record", result["reason"])

    def test_undeclared_inputs_fail(self):
        result = self.run_check(FakeGH(routes(caller({"with": {"runs-onn": "ubuntu-slim"}}))))
        self.assertFalse(result["ok"])
        self.assertIn("runs-onn", result["reason"])

    def test_required_input_value_must_be_the_pr_number_expression(self):
        for value in (0, "0", "${{ github.run_id }}", None):
            text = caller({"with": {"pull-request-number": value}})
            result = self.run_check(FakeGH(routes(text)))
            self.assertFalse(result["ok"], value)
            self.assertIn("pull-request-number", result["reason"])
        unknown = json.loads(HUB_ALLOWLIST)
        unknown["on"]["workflow_call"]["inputs"]["token-name"] = {"required": True}
        self.assertFalse(self.run_check(FakeGH(routes(caller(), hub_text=json.dumps(unknown))))["ok"])

    def test_explicit_runner_through_the_pinned_hub_mapping(self):
        gh = FakeGH(routes(caller(self.SLIM)))
        result = self.run_check(gh, label="ubuntu-slim")
        self.assertTrue(result["ok"], result)
        self.assertEqual((result["runner"], result["runner_source"], result["hub_sha"]),
                         ("ubuntu-slim", "with.runs-on", HUB_SHA))
        self.assertEqual(gh.calls.count(f"repos/{REPO}/branches/main"), 2)

    def test_hub_default_runner_counts_when_the_caller_sets_none(self):
        result = self.run_check(FakeGH(routes(caller(), hub_text=HUB_DEFAULT_SLIM)), label="ubuntu-slim")
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["runner_source"], "hub default")
        self.assertFalse(self.run_check(FakeGH(routes(caller())), label="ubuntu-slim")["ok"])

    def test_input_the_pinned_hub_ignores_does_not_count(self):
        self.assertFalse(self.run_check(FakeGH(routes(caller(self.SLIM), hub_text=hub("ubuntu-24.04"))),
                                        label="ubuntu-slim")["ok"])
        self.assertFalse(self.run_check(FakeGH(routes(caller(self.SLIM), hub_text="")), label="ubuntu-slim")["ok"])

    def test_another_jobs_runner_or_an_expression_does_not_count(self):
        other = caller(jobs={"lint": {"runs-on": "ubuntu-slim", "steps": [{"run": "true"}]}})
        self.assertFalse(self.run_check(FakeGH(routes(other)), label="ubuntu-slim")["ok"])
        result = self.run_check(FakeGH(routes(caller({"with": {"runs-on": "${{ vars.RUNNER }}"}}))), label="ubuntu-slim")
        self.assertIn("not a literal", result["reason"])

    def test_text_in_a_string_cannot_pose_as_the_call(self):
        text = json.dumps({"on": "pull_request", "env": {"NOTE": f"uses: {PIN}"},
                           "jobs": {"lint": {"runs-on": "ubuntu-slim", "steps": [{"run": "true"}]}}})
        self.assertFalse(self.run_check(FakeGH(routes(text)))["ok"])

    def test_extra_unpinned_or_differently_cased_calls_fail(self):
        for extra in ({"second": {"uses": f"Merglbot-Core/GitHub/.github/workflows/pr-gate.yml@{HUB_SHA}"}},
                      {"second": {"uses": "merglbot-core/github/.github/workflows/pr-gate.yml@main"}}):
            self.assertFalse(self.run_check(FakeGH(routes(caller(jobs=extra))))["ok"])
        self.assertFalse(self.run_check(FakeGH(routes(
            caller(uses="merglbot-core/github/.github/workflows/pr-gate.yml@main"))))["ok"])

    def test_caller_must_provably_run_on_pull_requests_to_main(self):
        cases = [caller(on="workflow_dispatch"),
                 caller(on={"pull_request": {"branches": ["release"]}}),
                 caller(on={"pull_request": {"branches": ["**", "!main"]}}),
                 caller(on={"pull_request": {"branches-ignore": ["**"]}}),
                 caller(on={"pull_request": {"paths": ["src/**"]}}),
                 caller(on={"pull_request": {"types": ["labeled"]}}),
                 caller({"if": "false"}),
                 caller({"if": "${{ false && github.event_name == 'pull_request' }}"}),
                 caller({"needs": ["prep"]}, jobs={"prep": {"if": "false", "runs-on": "ubuntu-24.04",
                                                            "steps": [{"run": "true"}]}})]
        for text in cases:
            result = self.run_check(FakeGH(routes(text)))
            self.assertFalse(result["ok"], text)
            self.assertIn("pull requests", result["reason"])
        self.assertTrue(self.run_check(FakeGH(routes(caller(on={"pull_request": {
            "branches": ["main"], "types": ["opened", "synchronize", "reopened"]}}))))["ok"])

    def test_required_hub_inputs_must_be_passed(self):
        text = json.dumps({"on": "pull_request", "jobs": {"pr-gate": {"uses": PIN, "with": {"runs-on": "ubuntu-slim"}}}})
        result = self.run_check(FakeGH(routes(text)))
        self.assertFalse(result["ok"])
        self.assertIn("pull-request-number", result["reason"])
        self.assertTrue(self.run_check(FakeGH(routes(caller(self.SLIM))), label="ubuntu-slim")["ok"])

    def test_missing_pinned_hub_workflow_fails_without_a_label(self):
        result = self.run_check(FakeGH(routes(caller(), hub_text="")))
        self.assertFalse(result["ok"])
        self.assertIn("pinned hub workflow", result["reason"])
        self.assertTrue(self.run_check(FakeGH(routes(caller())))["ok"])

    def test_unexpected_shapes_report_instead_of_raising(self):
        broken = {f"repos/{REPO}/branches/main": {"commit": {"sha": MAIN}},
                  f"repos/{REPO}/contents/.github/workflows?ref={MAIN}": [{"path": 1}]}
        self.assertFalse(self.run_check(FakeGH(broken), pr_record={"expected_files": [3]})["ok"])
        self.assertFalse(ca.owner_exception_live_ok(FakeGH({}), {"owner_exception": {"pr": 5, "required_contexts": ["x"]}})[0])
        self.assertFalse(ca.billing_retry_blocked(None, ca.parse_utc("2026-10-09T08:00:00Z")))
        self.assertIn("bez zaznamenaného", ca.exception_notes([{"owner_exception": {"text": "x"}, "owner_exception_check": {"reason": None}}], "895"))

    def test_rollout_files_must_match(self):
        record = {"expected_files": [".github/workflows/pr-gate.yml", ".github/workflows/gitleaks-weekly.yml"],
                  "deleted_files": [".github/workflows/security-gitleaks.yml"]}
        listing = [{"path": ".github/workflows/pr-gate.yml"}, {"path": ".github/workflows/security-gitleaks.yml"}]
        result = self.run_check(FakeGH(routes(caller(), listing=listing)), pr_record=record)
        self.assertIn("gitleaks-weekly.yml", result["reason"])
        self.assertIn("security-gitleaks.yml", result["reason"])

    def test_moving_or_unreadable_main_fails(self):
        mains = [{"commit": {"sha": MAIN}}, {"commit": {"sha": MOVED}}]
        self.assertIn("moved", self.run_check(FakeGH(routes(caller(), mains=mains)))["reason"])
        self.assertFalse(self.run_check(FakeGH({}))["ok"])


class OwnerException(unittest.TestCase):
    ITEM = {"repo": "d/acq", "owner_exception": {
        "text": "Výjimka jako #892", "decided_at": "2026-09-30T18:44:00Z", "decided_at_prague": "30. 9. 2026",
        "basis": "Gitleaks zůstává povinný.", "pr": "d/acq#170", "required_contexts": ["gitleaks / Secret Scanning"]}}

    def gh(self, merged=False, contexts=("gitleaks / Secret Scanning", "ci")):
        return FakeGH({"repos/d/acq/pulls/170": {"state": "closed", "merged": merged},
                       "repos/d/acq/branches/main/protection/required_status_checks":
                       {"checks": [{"context": c} for c in contexts]}})

    def test_closed_unmerged_with_contexts_passes(self):
        ok, reason = ca.owner_exception_live_ok(self.gh(), self.ITEM)
        self.assertTrue(ok, reason)

    def test_merged_pr_or_lost_context_fails(self):
        self.assertFalse(ca.owner_exception_live_ok(self.gh(merged=True), self.ITEM)[0])
        self.assertFalse(ca.owner_exception_live_ok(self.gh(contexts=("ci",)), self.ITEM)[0])
        self.assertFalse(ca.owner_exception_live_ok(self.gh(), {"owner_exception": {"text": "x"}})[0])
        bad = {"owner_exception": dict(self.ITEM["owner_exception"], required_contexts="gitleaks / Secret Scanning")}
        self.assertFalse(ca.owner_exception_live_ok(self.gh(), bad)[0])

    def test_rows_and_one_note_per_decision(self):
        second = dict(self.ITEM, repo="p/acq")
        self.assertIn("zavřen bez merge", ca.exception_row(self.ITEM))
        self.assertIn("zavřen bez merge", ca.exception_row({"owner_exception": self.ITEM["owner_exception"]}))
        checked = dict(self.ITEM, owner_exception_check={"reason": "d/acq#170 zavřen bez merge"})
        notes = ca.exception_notes([checked, second], "895")
        self.assertEqual(notes.count("**Výjimka ownera**"), 1)
        self.assertIn("Při uzavření živě ověřeno", notes)
        self.assertIn("Měření pokračuje", ca.exception_notes([self.ITEM], "892"))


class LowTrafficAcceptance(unittest.TestCase):
    def test_only_a_passed_live_check_verifies_a_low_traffic_row(self):
        legacy = {"met_at": "t", "low_traffic": True, "observed": 0}
        for row in (legacy, dict(legacy, low_traffic_check={"ok": False}), dict(legacy, low_traffic_check={"ok": "yes"}),
                    dict(legacy, low_traffic_check="ok")):
            self.assertTrue(ca.unverified_low_traffic(row), row)
        for row in (dict(legacy, low_traffic_check={"ok": True}), {"met_at": "t"}, {}, None, "row"):
            self.assertFalse(ca.unverified_low_traffic(row), row)

    def test_invalidation_drops_only_an_unverified_acceptance(self):
        legacy = {"met_at": "t", "low_traffic": True, "observed": 0, "runs": {"1": {}}}
        self.assertTrue(ca.invalidate_unverified_low_traffic(legacy))
        self.assertEqual(legacy, {"runs": {"1": {}}, "low_traffic_invalidated": True})
        verified = {"met_at": "t", "low_traffic": True, "low_traffic_check": {"ok": True}}
        self.assertFalse(ca.invalidate_unverified_low_traffic(verified))
        self.assertEqual(verified["met_at"], "t")


class LowTrafficFreshnessAndClose(unittest.TestCase):
    NOW = dt.datetime(2026, 10, 7, tzinfo=dt.timezone.utc)

    def test_a_check_older_than_the_ttl_or_without_a_time_is_not_fresh(self):
        row = {"met_at": "t", "low_traffic": True}
        fresh = dict(row, low_traffic_check={"ok": True, "checked_at": "2026-10-06T12:00:00Z"})
        self.assertFalse(ca.unverified_low_traffic(fresh, self.NOW))
        self.assertFalse(ca.unverified_low_traffic(fresh))
        for check in ({"ok": True, "checked_at": "2026-10-05T23:00:00Z"}, {"ok": True}, {"ok": True, "checked_at": 5}):
            self.assertTrue(ca.unverified_low_traffic(dict(row, low_traffic_check=check), self.NOW), check)
        stale = dict(row, low_traffic_check={"ok": True, "checked_at": "2026-10-01T00:00:00Z"})
        self.assertTrue(ca.invalidate_unverified_low_traffic(stale, self.NOW))
        self.assertNotIn("met_at", stale)

    def test_close_issue_done_needs_a_closed_readback_and_the_board(self):
        def run(initial, close_code, closes, board_ok, reads=None):
            issue = {"state": initial}
            calls = []

            def gh(*args):
                calls.append(args)
                if closes:
                    issue.update(state="closed", state_reason="completed")
                return close_code, "", ""
            gh_json = reads or (lambda path: dict(issue))
            return ca.close_issue_done(gh, gh_json, lambda n, o: board_ok, "o/r", 896, "done"), calls
        self.assertEqual(run("open", 0, True, True)[0], True)
        self.assertEqual(run("closed", 0, False, True, reads=lambda path: {"state": "closed", "state_reason": "completed"}),
                         (True, []))
        # closed as not planned (for instance by the owner) is not a delivered close
        self.assertEqual(run("closed", 0, False, True, reads=lambda path: {"state": "closed", "state_reason": "not_planned"}),
                         (False, []))
        self.assertFalse(run("open", 1, False, True)[0])
        self.assertFalse(run("open", 0, False, True)[0])
        self.assertFalse(run("open", 0, True, None)[0])
        self.assertFalse(run("open", 0, True, True, reads=lambda path: None)[0])
        self.assertFalse(run("open", 0, True, True, reads=lambda path: ["x"])[0])


class BillingCoverage(unittest.TestCase):
    WINDOW = ["2026-10-06", "2026-10-07"]
    MARK = ["2026-10-06", "2026-10-07"]
    NOW = dt.datetime(2026, 10, 11, 12, tzinfo=dt.timezone.utc)
    RAN = {"runner_id": 7, "steps": [{"name": "x"}], "conclusion": "cancelled"}
    QUEUED = {"runner_id": 0, "steps": [], "started_at": "2026-10-07T12:01:00Z", "conclusion": "cancelled"}

    def gh(self, listings, probes=None, jobs=None, calls=None):
        def read(path):
            if calls is not None:
                calls.append(path)
            repo = path.split("/actions/")[0][len("repos/"):]
            if "/jobs" in path:
                return (jobs or {}).get(path.split("/runs/")[1].split("/")[0])
            if "&status=" in path:
                status = path.split("&status=")[1].split("&")[0]
                return {"total_count": (probes or {}).get(repo, {}).get(status, 0), "workflow_runs": []}
            return listings.get(repo)
        return read

    def run_step(self, listings, usage, confirmed=None, budget=True, probes=None, jobs=None, now=None,
                 billable=None, calls=None, window=None):
        confirmed = {} if confirmed is None else confirmed
        return ca.billing_coverage_step(self.gh(listings, probes, jobs, calls), ["o/a", "o/b"], usage,
                                        window or self.WINDOW, confirmed, lambda: budget, now or self.NOW,
                                        billable), confirmed

    def runs(self, *rows, total=None):
        out = []
        for n, row in enumerate(rows):
            day, status, conclusion = row[:3]
            updated = row[3] if len(row) > 3 else f"{day}T12:10:00Z"
            out.append({"id": n, "created_at": f"{day}T12:00:00Z", "updated_at": updated,
                        "status": status, "conclusion": conclusion})
        return {"total_count": len(out) if total is None else total, "workflow_runs": out}

    def test_each_repository_must_reach_its_own_last_billable_run(self):
        listings = {"o/a": self.runs(("2026-10-07", "completed", "success"), ("2026-10-06", "completed", "failure")),
                    "o/b": self.runs(("2026-10-07", "completed", "skipped"), ("2026-10-06", "completed", "timed_out"))}
        (status, detail), confirmed = self.run_step(listings, {"o/x": {"2026-10-08": 1}, "o/a": {"2026-10-06": 1},
                                                               "o/b": {"2026-10-06": 1}})
        self.assertEqual((status, detail), ("lagging", ["o/a"]))
        self.assertEqual(confirmed, {"o/b": {"v": 6, "window": self.MARK, "last": "2026-10-06"}})
        (status, _), confirmed = self.run_step(listings, {"O/A": {"2026-10-08": 1}, "o/b": {"2026-10-06": 1}})
        self.assertEqual((status, confirmed["o/a"]), ("ok", {"v": 6, "window": self.MARK, "last": "2026-10-07"}))

    def test_the_window_and_every_listed_run_must_have_settled(self):
        # o/a's only run settled on 9 Oct, but the window (ends 8 Oct 00:00Z) settles on 11 Oct
        settled_run = {"o/a": self.runs(("2026-10-06", "completed", "success")), "o/b": self.runs()}
        early = dt.datetime(2026, 10, 10, 8, tzinfo=dt.timezone.utc)
        (status, detail), confirmed = self.run_step(settled_run, {"o/a": {"2026-10-06": 1}}, now=early)
        self.assertEqual((status, detail, confirmed), ("pending", ["o/a", "o/b"], {}))
        listings = {"o/a": self.runs(("2026-10-07", "completed", "success")), "o/b": self.runs()}
        usage = {"o/a": {"2026-10-07": 1}}
        self.assertEqual(self.run_step(listings, usage)[0], ("ok", None))

    def test_a_re_run_after_the_window_cannot_change_window_usage(self):
        # A run of the window re-run on 10 Oct bills on 10 Oct, outside the window: neither the
        # late finish nor a confirmation made before the re-run holds the window's verdict.
        rerun = {"o/a": self.runs(("2026-10-07", "completed", "success"),
                                  ("2026-10-06", "completed", "success", "2026-10-10T20:00:00Z")),
                 "o/b": self.runs()}
        usage = {"o/a": {"2026-10-07": 1, "2026-10-10": 3}}
        (status, _), confirmed = self.run_step(rerun, usage)
        self.assertEqual((status, confirmed["o/a"]["last"]), ("ok", "2026-10-07"))
        self.assertEqual(self.run_step({}, usage, confirmed=confirmed, budget=False)[0], ("ok", None))

    def test_a_cancelled_run_bills_only_when_a_job_ran_on_a_runner(self):
        listings = {"o/a": self.runs(("2026-10-07", "completed", "cancelled"), ("2026-10-06", "completed", "success")),
                    "o/b": self.runs()}
        usage = {"o/a": {"2026-10-06": 1}}
        ran = {"0": {"total_count": 1, "jobs": [self.RAN]}}
        self.assertEqual(self.run_step(listings, usage, jobs=ran)[0], ("lagging", ["o/a"]))
        queued = {"0": {"total_count": 1, "jobs": [self.QUEUED]}}
        (status, _), confirmed = self.run_step(listings, usage, jobs=queued)
        self.assertEqual((status, confirmed["o/a"]["last"]), ("ok", "2026-10-06"))
        self.assertEqual(self.run_step(listings, usage, jobs={})[0], ("error", "o/a"))
        partial = {"0": {"total_count": 150, "jobs": [self.QUEUED] * 100}}
        self.assertEqual(self.run_step(listings, usage, jobs=partial)[0], ("error", "o/a"))
        uncounted = {"0": {"jobs": [self.QUEUED]}}
        self.assertEqual(self.run_step(listings, usage, jobs=uncounted)[0], ("error", "o/a"))

    def test_billability_is_cached_per_run_attempt_so_a_small_budget_progresses(self):
        listings = {"o/a": self.runs(("2026-10-07", "completed", "cancelled"), ("2026-10-06", "completed", "success")),
                    "o/b": self.runs()}
        cache, calls = {}, []
        queued = {"0": {"total_count": 1, "jobs": [self.QUEUED]}}
        self.run_step(listings, {"o/a": {"2026-10-06": 1}}, jobs=queued, billable=cache)
        self.assertEqual(cache, {"o/a#0#1": False, "o/a#1#1": True})
        self.run_step(listings, {"o/a": {"2026-10-06": 1}}, jobs=queued, billable=cache, calls=calls)
        self.assertFalse(any("/jobs" in path for path in calls))

    def test_a_running_run_holds_coverage_until_it_finished_and_its_usage_arrived(self):
        confirmed = {}
        quiet_b = self.runs()
        running = {"o/a": self.runs(("2026-10-07", "in_progress", None), ("2026-10-06", "completed", "success")),
                   "o/b": quiet_b}
        usage = {"o/a": {"2026-10-06": 1}}
        self.assertEqual(self.run_step(running, usage, confirmed)[0], ("pending", ["o/a"]))
        self.assertNotIn("o/a", confirmed)
        finished = {"o/a": self.runs(("2026-10-07", "completed", "success"), ("2026-10-06", "completed", "success")),
                    "o/b": quiet_b}
        self.assertEqual(self.run_step(finished, usage, confirmed)[0], ("lagging", ["o/a"]))
        usage["o/a"]["2026-10-07"] = 2
        self.assertEqual(self.run_step(finished, usage, confirmed)[0], ("ok", None))
        self.assertEqual(confirmed["o/a"], {"v": 6, "window": self.MARK, "last": "2026-10-07"})

    def test_a_confirmation_is_revalidated_against_the_window_and_the_current_usage(self):
        done = {"o/a": {"v": 6, "window": self.MARK, "last": "2026-10-07"}, "o/b": {"v": 6, "window": self.MARK, "last": None}}
        self.assertEqual(self.run_step({}, {"o/a": {"2026-10-07": 1}}, confirmed=dict(done), budget=False)[0], ("ok", None))
        # the usage day behind the confirmation is gone from the current fetch
        self.assertEqual(self.run_step({}, {}, confirmed=dict(done), budget=False)[0], ("budget", "o/a"))
        # the window moved
        moved = ["2026-10-07", "2026-10-08"]
        late = dt.datetime(2026, 10, 12, 12, tzinfo=dt.timezone.utc)
        self.assertEqual(self.run_step({}, {"o/a": {"2026-10-08": 1}}, confirmed=dict(done), budget=False,
                                       window=moved, now=late)[0], ("budget", "o/a"))

    def test_a_busy_repository_asks_every_unfinished_status(self):
        busy = {"o/a": self.runs(("2026-10-07", "completed", "success"), total=500), "o/b": self.runs()}
        usage = {"o/a": {"2026-10-07": 1}}
        self.assertEqual(self.run_step(busy, usage, probes={"o/a": {"waiting": 1}})[0], ("pending", ["o/a"]))
        self.assertEqual(self.run_step(busy, usage)[0], ("ok", None))

    def test_a_quiet_window_needs_no_charge_row(self):
        quiet = {"o/a": self.runs(), "o/b": self.runs(("2026-10-07", "completed", "skipped"))}
        (status, _), confirmed = self.run_step(quiet, {})
        self.assertEqual((status, confirmed), ("ok", {"o/a": {"v": 6, "window": self.MARK, "last": None},
                                                      "o/b": {"v": 6, "window": self.MARK, "last": None}}))

    def test_budget_errors_and_confirmation_versions(self):
        ambiguous = {"o/a": self.runs(("2026-10-07", "completed", "skipped"), total=150), "o/b": self.runs()}
        self.assertEqual(self.run_step(ambiguous, {})[0], ("error", "o/a"))
        self.assertEqual(self.run_step({"o/b": self.runs()}, {})[0], ("error", "o/a"))
        self.assertEqual(self.run_step({"o/a": self.runs(), "o/b": self.runs()}, {}, budget=False)[0], ("budget", "o/a"))
        old = {"o/a": {"v": 5, "window": self.MARK, "last": None}, "o/b": "2026-10-07"}
        self.assertEqual(self.run_step({}, {}, confirmed=old, budget=False)[0], ("budget", "o/a"))


class LiveChildren(unittest.TestCase):
    PROJECT, DONE = "PVT_x", "done"

    def check(self, rows, pages, extra=None):
        routes = {"repos/o/g/issues/888/sub_issues?per_page=100&page=1": rows}
        routes.update(extra or {})
        answers = list(pages)
        def graphql(query):
            return answers.pop(0) if answers else None
        return ca.live_children_done(FakeGH(routes), graphql, "o/g", 888, self.PROJECT, self.DONE)

    def row(self, n, state="closed", repo="o/g"):
        return {"number": n, "state": state, "repository_url": f"https://api.github.com/repos/{repo}"}

    def page(self, entries, cursor=None):
        nodes = [{"content": {"number": n, "repository": {"nameWithOwner": repo}},
                  "fieldValueByName": {"optionId": option}} for repo, n, option in entries]
        return {"node": {"items": {"pageInfo": {"hasNextPage": bool(cursor), "endCursor": cursor}, "nodes": nodes}}}

    def test_all_closed_and_done_across_project_pages(self):
        pages = [self.page([("o/g", 889, "done"), ("x/y", 5, "todo")], cursor="c1"),
                 self.page([("o/g", 909, "done")])]
        ok, reason = self.check([self.row(889), self.row(909)], pages)
        self.assertTrue(ok, reason)

    def test_open_missing_duplicated_or_not_done_child_blocks(self):
        self.assertFalse(self.check([self.row(909, state="open")], [self.page([("o/g", 909, "done")])])[0])
        self.assertFalse(self.check([self.row(909)], [self.page([("o/g", 909, "todo")])])[0])
        self.assertFalse(self.check([self.row(909)], [self.page([("o/g", 889, "done")])])[0])
        self.assertFalse(self.check([self.row(909)], [self.page([("o/g", 909, "done"), ("o/g", 909, "done")])])[0])
        self.assertFalse(self.check([self.row(909)], [None])[0])

    def test_child_in_another_repository_is_matched_by_repository(self):
        pages = [self.page([("x/y", 5, "done")])]
        self.assertTrue(self.check([self.row(5, repo="x/y")], pages)[0])
        self.assertFalse(self.check([self.row(5, repo="x/y")], [self.page([("o/g", 5, "done")])])[0])

    def test_incomplete_pagination_or_missing_done_option_blocks(self):
        broken = {"node": {"items": {"pageInfo": {}, "nodes": [{"content": {"number": 909, "repository": {"nameWithOwner": "o/g"}},
                                                                   "fieldValueByName": {"optionId": "done"}}]}}}
        self.assertFalse(self.check([self.row(909)], [broken])[0])
        more = self.page([("o/g", 909, "done")], cursor=None)
        more["node"]["items"]["pageInfo"] = {"hasNextPage": True, "endCursor": None}
        self.assertFalse(self.check([self.row(909)], [more])[0])
        routes = {"repos/o/g/issues/888/sub_issues?per_page=100&page=1": [self.row(909)]}
        nostatus = lambda q: {"node": {"items": {"pageInfo": {"hasNextPage": False}, "nodes": [
            {"content": {"number": 909, "repository": {"nameWithOwner": "o/g"}}, "fieldValueByName": None}]}}}
        self.assertFalse(ca.live_children_done(FakeGH(routes), nostatus, "o/g", 888, self.PROJECT, None)[0])

    def test_second_sub_issue_page_is_read(self):
        first = [self.row(n) for n in range(1, 101)]
        extra = {"repos/o/g/issues/888/sub_issues?per_page=100&page=2": [self.row(909, state="open")]}
        self.assertFalse(self.check(first, [self.page([])], extra)[0])


class Python39(unittest.TestCase):
    def test_module_parses_as_python_3_9(self):
        import ast
        source = (ROOT / "tools/gh-cost-autopilot-adoption/cost_adoption.py").read_text()
        tree = ast.parse(source, feature_version=(3, 9))
        attrs = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
        self.assertNotIn("UTC", attrs)  # datetime.UTC is 3.11+
        self.assertFalse(any(isinstance(node, ast.Match) for node in ast.walk(tree))
                         if hasattr(ast, "Match") else False)


if __name__ == "__main__":
    unittest.main()
