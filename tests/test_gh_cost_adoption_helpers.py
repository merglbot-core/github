"""Tests for tools/gh-cost-autopilot-adoption/cost_adoption.py (owner plan of 30 Sep 2026)."""
import base64
import datetime as dt
import importlib.util
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "cost_adoption", ROOT / "tools/gh-cost-autopilot-adoption/cost_adoption.py")
ca = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ca)

MAIN, MOVED, HUB_SHA = "a" * 40, "b" * 40, "c" * 40
REPO = "o/r"


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


def caller(extra="", uses=None):
    uses = uses or f"merglbot-core/github/.github/workflows/pr-gate.yml@{HUB_SHA}"
    return f"name: PR Gate\non: pull_request\njobs:\n  pr-gate:\n    uses: {uses}\n{extra}"


def routes(caller_text, listing=None, mains=None, hub_text=None):
    base = {
        f"repos/{REPO}/branches/main": Seq(mains or [{"commit": {"sha": MAIN}}, {"commit": {"sha": MAIN}}]),
        f"repos/{REPO}/contents/.github/workflows?ref={MAIN}": listing if listing is not None else
        [{"path": ".github/workflows/pr-gate.yml"}, {"path": ".github/workflows/ci.yml"}],
        f"repos/{REPO}/contents/.github/workflows/pr-gate.yml?ref={MAIN}": content(caller_text),
    }
    if hub_text is not None:
        base[f"repos/merglbot-core/github/contents/.github/workflows/pr-gate.yml?ref={HUB_SHA}"] = content(hub_text)
    return base


HUB_DEFAULT_SLIM = ("on:\n  workflow_call:\n    inputs:\n      runs-on:\n        description: runner\n"
                    "        type: string\n        default: ubuntu-slim\n      mode:\n        default: advisory\n")


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
        self.assertTrue(ca.closes_billing("PARTIAL"))
        self.assertFalse(ca.closes_billing("BEZ ÚSPORY"))
        self.assertIn("30. 9. 2026", ca.verdict_note("PARTIAL"))

    def test_retry_and_data_gap_timing(self):
        now = ca.parse_utc("2026-10-09T08:00:00Z")
        self.assertTrue(ca.billing_retry_blocked({"retry_after": "2026-10-09T08:30:00Z"}, now))
        self.assertFalse(ca.billing_retry_blocked({"retry_after": "2026-10-09T07:59:00Z"}, now))
        self.assertFalse(ca.billing_retry_blocked({}, now))
        self.assertFalse(ca.billing_data_gap_due("2026-10-09T06:00:00Z", now))
        self.assertTrue(ca.billing_data_gap_due("2026-10-06T06:00:00Z", now))

    def test_hub_calls_ignore_comments_short_shas_and_branches(self):
        text = (f"    uses: merglbot-core/github/.github/workflows/pr-gate.yml@{HUB_SHA}  # pinned\n"
                f"#   uses: merglbot-core/github/.github/workflows/pr-gate.yml@{MAIN}\n"
                "    uses: merglbot-core/github/.github/workflows/pr-gate.yml@main\n"
                "    uses: merglbot-core/github/.github/workflows/pr-gate.yml@c17b925\n")
        self.assertEqual(ca.hub_calls(text), [HUB_SHA])

    def test_input_default_reads_only_the_named_block(self):
        self.assertEqual(ca.input_default(HUB_DEFAULT_SLIM, "runs-on"), "ubuntu-slim")
        self.assertEqual(ca.input_default(HUB_DEFAULT_SLIM, "mode"), "advisory")
        self.assertIsNone(ca.input_default(HUB_DEFAULT_SLIM, "absent"))


class LiveCallerConfig(unittest.TestCase):
    def run_check(self, gh, **kw):
        return ca.live_caller_config(gh, REPO, "pr-gate.yml", **kw)

    def test_explicit_runner_is_verified_and_main_reread(self):
        gh = FakeGH(routes(caller("    with:\n      runs-on: ubuntu-slim\n")))
        result = self.run_check(gh, label="ubuntu-slim")
        self.assertTrue(result["ok"], result)
        self.assertEqual((result["runner"], result["runner_source"], result["hub_sha"]),
                         ("ubuntu-slim", "with.runs-on", HUB_SHA))
        self.assertEqual(gh.calls.count(f"repos/{REPO}/branches/main"), 2)

    def test_hub_default_runner_counts_when_the_caller_sets_none(self):
        gh = FakeGH(routes(caller(), hub_text=HUB_DEFAULT_SLIM))
        result = self.run_check(gh, label="ubuntu-slim")
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["runner_source"], "hub default")

    def test_other_runner_fails(self):
        result = self.run_check(FakeGH(routes(caller("    with:\n      runs-on: ubuntu-24.04\n"))),
                                label="ubuntu-slim")
        self.assertFalse(result["ok"])
        self.assertIn("ubuntu-24.04", result["reason"])

    def test_rollout_files_must_match(self):
        record = {"expected_files": [".github/workflows/pr-gate.yml", ".github/workflows/gitleaks-weekly.yml"],
                  "deleted_files": [".github/workflows/security-gitleaks.yml"]}
        listing = [{"path": ".github/workflows/pr-gate.yml"}, {"path": ".github/workflows/security-gitleaks.yml"}]
        result = self.run_check(FakeGH(routes(caller(), listing=listing)), pr_record=record)
        self.assertFalse(result["ok"])
        self.assertIn("gitleaks-weekly.yml", result["reason"])
        self.assertIn("security-gitleaks.yml", result["reason"])

    def test_two_hub_calls_fail(self):
        text = caller() + f"  second:\n    uses: merglbot-core/github/.github/workflows/pr-gate.yml@{HUB_SHA}\n"
        self.assertFalse(self.run_check(FakeGH(routes(text)))["ok"])

    def test_moving_main_fails(self):
        mains = [{"commit": {"sha": MAIN}}, {"commit": {"sha": MOVED}}]
        result = self.run_check(FakeGH(routes(caller(), mains=mains)))
        self.assertFalse(result["ok"])
        self.assertIn("moved", result["reason"])

    def test_unreadable_main_fails_closed(self):
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

    def test_rows_and_one_note_per_decision(self):
        second = dict(self.ITEM, repo="p/acq")
        self.assertIn("zavřen bez merge", ca.exception_row(self.ITEM))
        notes = ca.exception_notes([self.ITEM, second], "895")
        self.assertEqual(notes.count("**Výjimka ownera**"), 1)
        self.assertIn("Při uzavření živě ověřeno", notes)
        self.assertIn("Měření pokračuje", ca.exception_notes([self.ITEM], "892"))


class LiveChildren(unittest.TestCase):
    PROJECT, DONE = "PVT_x", "done"

    def check(self, rows, statuses, pages=None):
        routes = {"repos/o/g/issues/888/sub_issues?per_page=100&page=1": rows}
        routes.update(pages or {})
        def graphql(query):
            if statuses is None:
                return None
            return {"repository": {f"i{n}": {"projectItems": {"nodes": nodes}} for n, nodes in statuses.items()}}
        return ca.live_children_done(FakeGH(routes), graphql, "o/g", 888, self.PROJECT, self.DONE)

    def row(self, n, state="closed", repo="o/g"):
        return {"number": n, "state": state, "repository_url": f"https://api.github.com/repos/{repo}"}

    def done(self, option="done"):
        return [{"project": {"id": self.PROJECT}, "fieldValueByName": {"optionId": option}}]

    def test_all_closed_and_done(self):
        ok, reason = self.check([self.row(889), self.row(909)], {889: self.done(), 909: self.done()})
        self.assertTrue(ok, reason)

    def test_open_foreign_or_not_done_child_blocks(self):
        self.assertFalse(self.check([self.row(909, state="open")], {909: self.done()})[0])
        self.assertFalse(self.check([self.row(5, repo="x/y")], {5: self.done()})[0])
        self.assertFalse(self.check([self.row(909)], {909: self.done("todo")})[0])
        self.assertFalse(self.check([self.row(909)], None)[0])

    def test_second_page_is_read(self):
        first = [self.row(n) for n in range(1, 101)]
        pages = {"repos/o/g/issues/888/sub_issues?per_page=100&page=2": [self.row(909, state="open")]}
        statuses = {n: self.done() for n in range(1, 101)}
        self.assertFalse(self.check(first, statuses, pages)[0])


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
