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
        self.assertFalse(compare.introduced(report, shifted, step_maps={(
            ".github/workflows/ci.yml", "ci"): {1: 0}}))
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
        self.assertFalse(compare.introduced([original], [edited]))

    def test_same_step_and_expression_in_a_changed_command_is_new(self):
        fixture = Path(__file__).parent / "fixtures/workflow-lint/zizmor-1.30.1.json"
        old = next(x for x in json.loads(fixture.read_text()) if x["ident"] == "template-injection")
        new = copy.deepcopy(old)
        primary = next(loc for loc in new["locations"] if loc["symbolic"]["kind"] == "Primary")
        primary["concrete"]["feature"] = primary["concrete"]["feature"].replace("echo ", "printf ")
        self.assertTrue(compare.introduced([old], [new], step_maps={(
            ".github/workflows/ci.yml", "ci"): {0: 0}}))

    def test_compound_or_quoted_command_changes_keep_full_feature(self):
        command = 'echo "${{ github.event.pull_request.title }}"'
        for prefix in ('cd subdir; ', 'echo "a;b"; ', "echo hello && ", "set -e; "):
            with self.subTest(prefix=prefix):
                self.assertNotEqual(compare.command_identity(prefix + command),
                                    compare.command_identity(command))

    def test_anonymous_steps_keep_distinct_occurrences(self):
        old = [{"run": "old"}, {"run": "safe"}]
        new = [{"run": "safe"}, {"run": "old"}]
        self.assertEqual(compare.step_correspondence(old, new), {0: 0, 1: 1})
        fixture = Path(__file__).parent / "fixtures/workflow-lint/zizmor-1.30.1.json"
        original = next(x for x in json.loads(fixture.read_text()) if x["ident"] == "template-injection")
        moved = copy.deepcopy(original)
        for loc in moved["locations"]:
            for part in loc["symbolic"]["route"]["route"]:
                if "Index" in part:
                    part["Index"] = 1
        maps = {(".github/workflows/ci.yml", "ci"): compare.step_correspondence(old, new)}
        self.assertTrue(compare.introduced([original], [moved], step_maps=maps))

    def test_step_correspondence_after_insert_rename_or_duplicate(self):
        base = [{"id": "build", "run": "x"}, {"name": "display", "run": "y"}]
        new = [{"run": "unrelated"}, {"name": "renamed", "run": "y"}, base[0]]
        self.assertEqual(compare.step_correspondence(base, new), {2: 0, 1: 1})
        self.assertEqual(compare.step_correspondence([{"run": "x"}], [{"run": "x"}] * 2), {0: 0})

    def test_unique_names_follow_reordered_steps_and_tolerate_display_edits(self):
        base = [{"name": "first", "run": "x"}, {"name": "second", "run": "y"}]
        reordered = [{"name": "second", "run": "x"}, {"name": "first", "run": "y"}]
        self.assertEqual(compare.step_correspondence(base, reordered), {0: 1, 1: 0})
        renamed = [{"name": "display only", "run": "x"}, base[1]]
        self.assertEqual(compare.step_correspondence(base, renamed), {0: 0, 1: 1})

    def test_edit_and_insert_do_not_orphan_unchanged_sibling(self):
        old = [{"run": "first"}, {"run": "second"}]
        new = [old[0], {"run": "edited"}, {"run": "added"}]
        self.assertEqual(compare.step_correspondence(old, new), {0: 0})
        new[0] = {"id": "new_id", **old[0]}
        self.assertEqual(compare.step_correspondence(old, new), {0: 0})

    def test_execution_context_is_required_even_for_named_steps(self):
        for identity in ({"id": "build"}, {"name": "Build"}, {}):
            with self.subTest(identity=identity):
                old = [{**identity, "shell": "bash", "run": "same"}]
                new = [{**identity, "shell": "pwsh", "run": "same"}]
                self.assertEqual(compare.step_correspondence(old, new), {})

    def test_implicit_runner_changes_or_expressions_do_not_inherit(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ci.yml"
            def steps(runner):
                path.write_text("jobs:\n  a:\n    runs-on: '" + runner +
                                "'\n    steps: [{run: demo}, {run: demo, shell: bash}]\n")
                return compare.job_steps(path)["a"]
            ubuntu = steps("ubuntu-latest")
            self.assertEqual(compare.step_correspondence(ubuntu, ubuntu), {0: 0, 1: 1})
            self.assertEqual(compare.step_correspondence(ubuntu, steps("windows-latest")), {1: 1})
            dynamic = steps("${{ matrix.runner }}")
            self.assertEqual(compare.step_correspondence(dynamic, dynamic), {1: 1})

    def test_prelude_and_sibling_insertion_keep_baseline_route(self):
        report = json.loads((Path(__file__).parent / "fixtures/workflow-lint/zizmor-1.30.1.json").read_text())
        old = next(x for x in report if x["ident"] == "template-injection")
        new = copy.deepcopy(old)
        loc = next(x for x in new["locations"] if x["symbolic"]["kind"] == "Primary")
        command = loc["concrete"]["feature"]
        changed = "echo harmless; " + command
        loc["concrete"]["feature"] = changed
        loc["symbolic"]["route"]["route"][3]["Index"] = 1
        matched = compare.step_correspondence([{"run": command}], [{"run": "other"}, {"run": changed}])
        self.assertEqual(matched, {1: 0})
        self.assertFalse(compare.introduced([old], [new], step_maps={(".github/workflows/ci.yml", "ci"): matched}))

    def test_incompatible_duplicate_does_not_consume_baseline(self):
        self.assertEqual(compare.step_correspondence([{"id": "a", "run": "demo"}],
                         [{"id": "b", "run": "demo"}, {"run": "demo"}]), {1: 0})

    def test_job_steps_reads_yaml_as_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ci.yml"
            path.write_text("jobs:\n  ci:\n    steps:\n      - id: stable\n        run: example\n")
            self.assertEqual(compare.job_steps(path), {"ci": [{"id": "stable", "run": "example"}]})

    def test_identical_steps_are_paired_in_occurrence_order(self):
        step = {"run": "example"}
        self.assertEqual(compare.step_correspondence([step, step], [{"run": "other"}, step, step]),
                         {1: 0, 2: 1})
        self.assertEqual(compare.step_correspondence([step, step], [step, step, step]),
                         {0: 0, 1: 1})

    def test_run_defaults_use_step_then_job_then_workflow_precedence(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ci.yml"
            path.write_text("defaults: {run: {shell: bash, working-directory: root}}\n"
                            "jobs:\n  a:\n    steps: [{run: example}]\n"
                            "  b:\n    defaults: {run: {shell: sh}}\n"
                            "    steps: [{run: example}, {run: example, shell: pwsh}, {uses: demo}]\n")
            base = compare.job_steps(path)
            path.write_text(path.read_text().replace("shell: bash", "shell: python"))
            head = compare.job_steps(path)
            self.assertEqual(compare.step_correspondence(base["a"], head["a"]), {})
            self.assertEqual(base["b"], [{"run": "example", "shell": "sh", "working-directory": "root"},
                                          {"run": "example", "shell": "pwsh", "working-directory": "root"},
                                          {"uses": "demo"}])
            self.assertEqual(base["b"], head["b"])

    def test_job_steps_preserve_string_ids_and_resolve_aliases(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ci.yml"
            path.write_text("jobs:\n  yes:\n    steps:\n      - &sample {id: on, run: demo}\n"
                            "  no:\n    steps: [*sample]\n  delegate:\n    uses: org/repo/path@ref\n")
            steps = compare.job_steps(path)
            self.assertEqual(steps, {"yes": [{"id": "on", "run": "demo"}],
                                     "no": [{"id": "on", "run": "demo"}], "delegate": []})

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
