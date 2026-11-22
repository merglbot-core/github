import copy
import json
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch
import subprocess
import tempfile


module_path = Path(__file__).resolve().parents[1] / "scripts/workflow-lint/compare.py"
spec = importlib.util.spec_from_file_location("workflow_lint_compare", module_path)
compare = importlib.util.module_from_spec(spec)
spec.loader.exec_module(compare)


def finding(rule="template-injection", severity="High", job="ci", line=20):
    return {"ident": rule, "determinations": {"severity": severity}, "locations": [{
        "symbolic": {"kind": "Primary", "annotation": "fixture",
                     "key": {"Local": {"verbatim_path": ".github/workflows/ci.yml"}},
                     "route": {"route": [{"Key": "jobs"}, {"Key": job}]}},
        "concrete": {"feature": "fixture", "location": {"start_point": {"row": line}}},
    }]}


class ComparisonTest(unittest.TestCase):
    def test_captured_pinned_scanner_report(self):
        fixture = Path(__file__).parent / "fixtures/workflow-lint/zizmor-1.30.1.json"
        report = json.loads(fixture.read_text())
        shifted = copy.deepcopy(report)
        for item in shifted:
            for loc in item["locations"]:
                for part in loc["symbolic"]["route"]["route"]:
                    if "Index" in part:
                        part["Index"] += 1
        self.assertFalse(compare.introduced(report, shifted))
        self.assertEqual({key[0] for key in compare.introduced([], report)},
                         {"template-injection", "excessive-permissions"})
        self.assertEqual(sum(compare.introduced(report, report + shifted).values()), 2)

    def test_matched_expression_and_step_context(self):
        fixture = Path(__file__).parent / "fixtures/workflow-lint/zizmor-1.30.1.json"
        original = next(x for x in json.loads(fixture.read_text())
                        if x["ident"] == "template-injection")
        edited = copy.deepcopy(original)
        for loc in edited["locations"]:
            feature = loc["concrete"]["feature"]
            if "echo " in feature:
                loc["concrete"]["feature"] = feature.replace("echo ", "echo harmless; echo ")
        self.assertFalse(compare.introduced([original], [edited]))
        for loc in edited["locations"]:
            if loc["symbolic"]["kind"] == "Hidden":
                loc["concrete"]["feature"] = loc["concrete"]["feature"].replace(
                    "name: Static input", "name: Another step")
        self.assertTrue(compare.introduced([original], [edited]))

    def test_rename_keeps_findings_but_new_file_does_not(self):
        old = b".github/workflows/ci.yml"
        new = b".github/workflows/renamed.yml"
        added = b".github/workflows/new.yml"
        changes = b"\0".join([b"R100", old, new, b"A", added, b""])
        base, head, renames = compare.workflow_paths(changes)
        self.assertEqual(base, {old})
        self.assertEqual(head, {new, added})
        original = finding()
        renamed = copy.deepcopy(original)
        renamed["locations"][0]["symbolic"]["key"]["Local"]["verbatim_path"] = new.decode()
        self.assertFalse(compare.introduced([original], [renamed], renames))
        self.assertTrue(compare.introduced([], [renamed], renames))

    def test_baseline_parse_error_is_local_to_one_workflow(self):
        paths = [b".github/workflows/bad.yml", b".github/workflows/valid.yml"]
        entries = b"".join(b"100644 blob abc\t" + name + b"\0" for name in paths)
        parse_error = subprocess.CompletedProcess([], 3, b"", b"failed to parse input: bad YAML\nerror: no inputs collected")
        good = subprocess.CompletedProcess([], 0, json.dumps([finding()]).encode(), b"")
        with tempfile.TemporaryDirectory() as tmp, patch.object(
            compare.subprocess, "check_output", side_effect=[entries, b"[", b"valid"]
        ), patch.object(compare.subprocess, "run", side_effect=[parse_error, good]):
            self.assertEqual(compare.scan_revision("base", Path(tmp), "medium", set(paths),
                                                   check_syntax=False), [finding()])

    def test_baseline_scanner_failure_still_blocks(self):
        path = b".github/workflows/ci.yml"
        failure = subprocess.CompletedProcess([], 3, b"", b"fatal: scanner unavailable")
        with tempfile.TemporaryDirectory() as tmp, patch.object(
            compare.subprocess, "check_output", side_effect=[b"100644 blob abc\t" + path + b"\0", b"valid"]
        ), patch.object(compare.subprocess, "run", return_value=failure):
            with self.assertRaises(subprocess.CalledProcessError):
                compare.scan_revision("base", Path(tmp), "medium", {path}, check_syntax=False)

    def test_line_shift_preserves_existing_finding(self):
        self.assertFalse(compare.introduced([finding()], [finding(line=24)]))

    def test_additional_occurrence_and_different_job_are_new(self):
        for head in ([finding(), finding()], [finding(job="deploy")]):
            with self.subTest(head=head):
                self.assertEqual(sum(compare.introduced([finding()], head).values()), 1)

    def test_permissions_and_warning_classes(self):
        self.assertTrue(compare.introduced([], [finding("excessive-permissions")]))
        self.assertFalse(compare.introduced([], [finding(severity="Medium"), finding("other")]))

    def test_incomplete_report_is_an_error(self):
        with self.assertRaises(ValueError):
            compare.introduced({}, [])
        item = finding()
        item["locations"] = []
        with self.assertRaises(ValueError):
            compare.introduced([], [item])
