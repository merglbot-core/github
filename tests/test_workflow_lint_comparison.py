import importlib.util
from pathlib import Path
import unittest


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
