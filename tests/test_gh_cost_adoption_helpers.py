"""Tests for tools/gh-cost-autopilot-adoption/cost_adoption.py (owner plan of 30 Sep 2026)."""
import base64
import datetime as dt
import importlib.util
import pathlib
import unittest

try:
    import yaml  # noqa: F401  (ships with the launchd Python the autopilots run on)
    HAS_YAML = True
except ImportError:
    HAS_YAML = False

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
    hub = hub_text if hub_text is not None else HUB_ALLOWLIST
    if hub:
        base[f"repos/merglbot-core/github/contents/.github/workflows/pr-gate.yml?ref={HUB_SHA}"] = content(hub)
    return base


HUB_DEFAULT_SLIM = ("on:\n  workflow_call:\n    inputs:\n      runs-on:\n        description: runner\n"
                    "        type: string\n        default: ubuntu-slim\n      mode:\n        default: advisory\n"
                    "jobs:\n  gate:\n    runs-on: ${{ inputs.runs-on }}\n")
# The pinned hub at c17b925b: allowlist expression, default ubuntu-24.04.
HUB_ALLOWLIST = ("on:\n  workflow_call:\n    inputs:\n      runs-on:\n        default: ubuntu-24.04\n"
                 "jobs:\n  pr-gate:\n    runs-on: ${{ inputs.runs-on == 'ubuntu-slim' && 'ubuntu-slim' || 'ubuntu-24.04' }}\n")


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
        self.assertEqual(ca.billing_verdict(0, 0), "BEZ ÚSPORY")  # V6 #969
        for value in (float("nan"), float("inf")):
            self.assertEqual(ca.billing_verdict(value, 400), "DATA_GAP")
            self.assertFalse(ca.closes_billing(ca.billing_verdict(400, value)))
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



@unittest.skipUnless(HAS_YAML, "PyYAML not installed")
class Structure(unittest.TestCase):
    def test_only_job_level_pinned_calls_count(self):
        text = (f"on: pull_request\nenv:\n  NOTE: |\n    uses: merglbot-core/github/.github/workflows/pr-gate.yml@{MAIN}\n"
                "jobs:\n"
                f"  gate:\n    uses: merglbot-core/github/.github/workflows/pr-gate.yml@{HUB_SHA}  # pinned\n"
                "  other:\n    runs-on: ubuntu-24.04\n    steps:\n      - run: |\n"
                f"          uses: merglbot-core/github/.github/workflows/pr-gate.yml@{MOVED}\n"
                "  branch:\n    uses: merglbot-core/github/.github/workflows/pr-gate.yml@main\n"
                "  short:\n    uses: merglbot-core/github/.github/workflows/pr-gate.yml@c17b925\n")
        doc, reason = ca.parse_workflow(text)
        # Every job-level call counts (the caller check then requires exactly one, SHA-pinned);
        # text in block scalars and step scripts never does.
        self.assertEqual([(j, ref) for j, ref, _ in ca.hub_jobs(doc)],
                         [("gate", HUB_SHA), ("branch", "main"), ("short", "c17b925")], reason)

    def test_hub_input_default(self):
        self.assertEqual(ca.hub_input_default(HUB_DEFAULT_SLIM, "runs-on"), "ubuntu-slim")
        self.assertEqual(ca.hub_input_default(HUB_DEFAULT_SLIM, "mode"), "advisory")
        self.assertIsNone(ca.hub_input_default(HUB_DEFAULT_SLIM, "absent"))
        expression = HUB_DEFAULT_SLIM.replace("default: ubuntu-slim", "default: ${{ vars.RUNNER }}")
        self.assertIsNone(ca.hub_input_default(expression, "runs-on"))

    def test_unparseable_or_jobless_workflow_fails(self):
        self.assertIsNone(ca.parse_workflow("jobs: [unclosed")[0])
        self.assertIsNone(ca.parse_workflow("name: x\n")[0])


@unittest.skipUnless(HAS_YAML, "PyYAML not installed")
class LiveCallerConfig(unittest.TestCase):
    SLIM = "    with:\n      runs-on: ubuntu-slim\n"

    def run_check(self, gh, **kw):
        return ca.live_caller_config(gh, REPO, "pr-gate.yml", **kw)

    def test_explicit_runner_through_the_pinned_hub_mapping(self):
        gh = FakeGH(routes(caller(self.SLIM), hub_text=HUB_ALLOWLIST))
        result = self.run_check(gh, label="ubuntu-slim")
        self.assertTrue(result["ok"], result)
        self.assertEqual((result["runner"], result["runner_source"], result["hub_sha"]),
                         ("ubuntu-slim", "with.runs-on", HUB_SHA))
        self.assertEqual(gh.calls.count(f"repos/{REPO}/branches/main"), 2)

    def test_hub_default_runner_counts_when_the_caller_sets_none(self):
        result = self.run_check(FakeGH(routes(caller(), hub_text=HUB_DEFAULT_SLIM)), label="ubuntu-slim")
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["runner_source"], "hub default")
        self.assertFalse(self.run_check(FakeGH(routes(caller(), hub_text=HUB_ALLOWLIST)), label="ubuntu-slim")["ok"])

    def test_input_the_pinned_hub_ignores_does_not_count(self):
        ignoring = HUB_ALLOWLIST.replace("${{ inputs.runs-on == 'ubuntu-slim' && 'ubuntu-slim' || 'ubuntu-24.04' }}",
                                         "ubuntu-24.04")
        self.assertFalse(self.run_check(FakeGH(routes(caller(self.SLIM), hub_text=ignoring)), label="ubuntu-slim")["ok"])
        self.assertFalse(self.run_check(FakeGH(routes(caller(self.SLIM), hub_text="")), label="ubuntu-slim")["ok"])

    def test_another_jobs_runner_or_an_expression_does_not_count(self):
        other = caller() + "  lint:\n    runs-on: ubuntu-slim\n    steps:\n      - run: true\n"
        self.assertFalse(self.run_check(FakeGH(routes(other, hub_text=HUB_ALLOWLIST)), label="ubuntu-slim")["ok"])
        expr = caller("    with:\n      runs-on: ${{ vars.RUNNER }}\n")
        result = self.run_check(FakeGH(routes(expr, hub_text=HUB_ALLOWLIST)), label="ubuntu-slim")
        self.assertIn("not a literal", result["reason"])

    def test_text_in_a_block_scalar_cannot_pose_as_the_call(self):
        text = ("on: pull_request\nenv:\n  NOTE: |\n"
                f"    uses: merglbot-core/github/.github/workflows/pr-gate.yml@{HUB_SHA}\n"
                "jobs:\n  lint:\n    runs-on: ubuntu-slim\n    steps:\n      - run: true\n")
        self.assertFalse(self.run_check(FakeGH(routes(text)))["ok"])

    def test_extra_or_unpinned_hub_calls_fail(self):
        for extra in (f"  second:\n    uses: Merglbot-Core/GitHub/.github/workflows/pr-gate.yml@{HUB_SHA}\n",
                      f"  second:\n    uses: merglbot-core/github/.github/workflows/pr-gate.yml@{HUB_SHA}\n",
                      "  second:\n    uses: merglbot-core/github/.github/workflows/pr-gate.yml@main\n"):
            self.assertFalse(self.run_check(FakeGH(routes(caller() + extra)))["ok"])
        self.assertFalse(self.run_check(FakeGH(routes(
            caller(uses="merglbot-core/github/.github/workflows/pr-gate.yml@main"))))["ok"])

    def test_caller_must_run_on_pull_requests_to_main(self):
        for text in (caller().replace("on: pull_request", "on: workflow_dispatch"),
                     caller().replace("on: pull_request", "on:\n  pull_request:\n    branches: [release]"),
                     caller().replace("on: pull_request", "on:\n  pull_request:\n    branches-ignore: ['**']"),
                     caller().replace("on: pull_request", "on:\n  pull_request:\n    paths: [src/**]"),
                     caller().replace("on: pull_request", "on:\n  pull_request:\n    types: [labeled]"),
                     caller("    if: false\n"),
                     caller("    if: ${{ false && github.event_name == 'pull_request' }}\n")):
            result = self.run_check(FakeGH(routes(text)))
            self.assertFalse(result["ok"])
            self.assertIn("pull requests", result["reason"])

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

    def test_rows_and_one_note_per_decision(self):
        second = dict(self.ITEM, repo="p/acq")
        self.assertIn("zavřen bez merge", ca.exception_row(self.ITEM))
        self.assertIn("zavřen bez merge", ca.exception_row({"owner_exception": self.ITEM["owner_exception"]}))
        notes = ca.exception_notes([self.ITEM, second], "895")
        self.assertEqual(notes.count("**Výjimka ownera**"), 1)
        self.assertIn("Při uzavření živě ověřeno", notes)
        self.assertIn("Měření pokračuje", ca.exception_notes([self.ITEM], "892"))


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
