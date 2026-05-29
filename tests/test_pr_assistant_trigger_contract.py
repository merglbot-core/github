import importlib.util
import pathlib
import sys
import unittest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
MANIFEST_HELPER = REPO_ROOT / "scripts" / "pr-assistant" / "repo-policy-manifest.py"
V3_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "merglbot-pr-assistant-v3-on-demand.yml"


def load_manifest_helper():
    spec = importlib.util.spec_from_file_location("repo_policy_manifest", MANIFEST_HELPER)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class TriggerContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.helper = load_manifest_helper()

    def test_invalid_v3_trigger_has_machine_readable_skip_receipt(self):
        workflow = V3_WORKFLOW.read_text(encoding="utf-8")

        self.assertIn("skip_reason: ${{ steps.get_pr.outputs.skip_reason }}", workflow)
        self.assertIn("trigger_contract_status: ${{ steps.get_pr.outputs.trigger_contract_status }}", workflow)
        self.assertIn("merglbot.pr_assistant.v3.trigger_skip.v1", workflow)
        self.assertIn("invalid_v3_review_trigger", workflow)
        self.assertIn("Merglbot PR Assistant v3 Trigger Skip", workflow)

    def test_active_owner_uses_v4_when_v3_disabled(self):
        owner, expected_check, signal = self.helper.determine_active_review_owner(
            enabled=True,
            workflow_present=True,
            v3_disabled_value="true",
        )

        self.assertEqual(owner, "v4")
        self.assertEqual(expected_check, "Merglbot PR Assistant v4")
        self.assertEqual(signal, "v3_disabled_variable_true")

    def test_branch_protection_mismatch_emits_contract_reason(self):
        result = self.helper.evaluate_branch_protection_review_owner(
            active_review_owner="v4",
            required_checks=["ci", "Merglbot PR Assistant v3"],
        )

        self.assertEqual(result["status"], "branch_protection_review_owner_mismatch")
        self.assertEqual(result["expected_check"], "Merglbot PR Assistant v4")
        self.assertEqual(result["mismatched_required_review_checks"], ["Merglbot PR Assistant v3"])

    def test_hard_gate_missing_required_review_check_is_mismatch(self):
        result = self.helper.evaluate_branch_protection_review_owner(
            active_review_owner="v4",
            required_checks=["ci"],
            review_owner_policy="hard_gate",
        )

        self.assertEqual(result["status"], "branch_protection_review_owner_mismatch")
        self.assertEqual(result["mismatch_reason"], "hard_gate_missing_required_review_check")
        self.assertEqual(result["expected_check"], "Merglbot PR Assistant v4")
        self.assertIn("add_required_status_check:Merglbot PR Assistant v4", result["remediation"])

    def test_advisory_repo_without_required_review_check_is_not_violation(self):
        result = self.helper.evaluate_branch_protection_review_owner(
            active_review_owner="v3",
            required_checks=["ci"],
            review_owner_policy="advisory",
        )

        self.assertEqual(result["status"], "advisory")
        self.assertEqual(result["mismatch_reason"], None)
        self.assertEqual(result["allowed_merge_policy"], "merglbot_signal_is_advisory_not_branch_protection_gate")

    def test_no_owner_repo_with_required_review_check_is_mismatch(self):
        result = self.helper.evaluate_branch_protection_review_owner(
            active_review_owner="none",
            required_checks=["ci", "Merglbot PR Assistant v3"],
            review_owner_policy="no_owner",
        )

        self.assertEqual(result["status"], "branch_protection_review_owner_mismatch")
        self.assertEqual(result["mismatch_reason"], "no_owner_policy_has_required_review_check")
        self.assertEqual(result["mismatched_required_review_checks"], ["Merglbot PR Assistant v3"])

    def test_branch_protection_alignment_accepts_matching_owner(self):
        result = self.helper.evaluate_branch_protection_review_owner(
            active_review_owner="v3",
            required_checks=["ci", "Merglbot PR Assistant v3"],
        )

        self.assertEqual(result["status"], "aligned")
        self.assertEqual(result["mismatched_required_review_checks"], [])

    def test_review_owner_alignment_payload_lists_excluded_orgs_and_classification_counts(self):
        rows = [
            {"repo": "example/a", "review_owner_policy": "advisory"},
            {"repo": "example/b", "review_owner_policy": "no_owner"},
        ]

        payload = self.helper.build_review_owner_alignment_payload(
            rows,
            [],
            excluded_orgs=["lrtch", "Merglevsky-cz"],
        )

        self.assertEqual(payload["schema_version"], 2)
        self.assertEqual(payload["delivery_scope"]["included_repo_count"], 2)
        self.assertEqual(payload["delivery_scope"]["excluded_orgs"], ["Merglevsky-cz", "lrtch"])
        self.assertEqual(payload["classification_counts"]["advisory"], 1)
        self.assertEqual(payload["classification_counts"]["no_owner"], 1)

    def test_git_blob_sha_matches_github_contents_sha_shape(self):
        self.assertEqual(
            self.helper.git_blob_sha(b"hello\n"),
            "ce013625030ba8dba906f756967f9e9ca394464a",
        )

    def test_private_org_variable_does_not_apply_to_public_repo(self):
        calls = []

        def fake_github_request(path, token, allow_http_statuses=()):
            calls.append((path, allow_http_statuses))
            if path.startswith("/repos/example/repo/actions/variables/"):
                return None
            if path.startswith("/orgs/example/actions/variables/"):
                return {"value": "true", "visibility": "private"}
            return None

        original = self.helper.github_request
        try:
            self.helper.github_request = fake_github_request
            value, source = self.helper.get_actions_variable(
                "example/repo",
                "MERGLBOT_PR_ASSISTANT_V3_DISABLED",
                "token",
                repo_private=False,
            )
        finally:
            self.helper.github_request = original

        self.assertIsNone(value)
        self.assertEqual(source, "org:private_not_applied")
        self.assertTrue(calls)


if __name__ == "__main__":
    unittest.main()
