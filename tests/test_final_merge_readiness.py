import unittest
import importlib.util
from pathlib import Path

POLICY_ENGINE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "policy-engine" / "final_merge_readiness.py"
spec = importlib.util.spec_from_file_location("final_merge_readiness", POLICY_ENGINE_PATH)
policy_engine = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(policy_engine)

DECISION_ALLOW = policy_engine.DECISION_ALLOW
DECISION_BLOCK = policy_engine.DECISION_BLOCK
DECISION_HUMAN_REQUIRED = policy_engine.DECISION_HUMAN_REQUIRED
evaluate_final_merge_readiness = policy_engine.evaluate_final_merge_readiness
evaluate_terraform_approval = policy_engine.evaluate_terraform_approval


MANIFEST = {
    "schema_version": "test",
    "policy_name": "test-policy",
    "default_merge_strategy": "squash",
    "receipt_ttl_minutes": 30,
    "repo_scope": {
        "included_orgs": ["merglbot-core", "merglbot-public"],
        "excluded_orgs": ["external-org"],
    },
    "path_policy": {
        "docs_only_globs": ["*.md", "docs/**", "ci-audit/**", "REPOSITORY_MAP.md"],
        "prompt_library_globs": ["prompt-library/assets/**", "apps/server/src/promptCatalog.ts"],
        "ai_data_policy_globs": [
            "MERGLBOT_AI_DATA_WORKFLOW_POLICY.md",
            "prompt-library/assets/**",
            "apps/server/src/dataExposurePolicy.ts",
        ],
        "workflow_globs": [".github/workflows/**", ".github/actions/**"],
        "terraform_globs": ["terraform/**", "**/*.tf", ".github/workflows/terraform-*.yml"],
        "denied_globs": ["**/*service-account*.json", "**/*.pem"],
    },
    "autonomous_actions": {
        "docs_only": ["final_merge_readiness"],
        "prompt_library": ["final_merge_readiness"],
        "workflow": [],
        "terraform": [],
    },
    "required_evidence": {
        "review_receipt": {
            "provider": "Merglbot PR Assistant",
            "must_match_head_sha": True,
            "accepted_statuses": ["success", "approved_for_closeout"],
        },
        "required_checks": {"accepted_conclusions": ["success", "neutral", "skipped"]},
        "docs_obligation": {"accepted_states": ["none", "not_required", "satisfied", "same_pr"]},
        "ai_data_policy_check": {"accepted_states": ["not_applicable", "passed", "aggregate_only"]},
    },
    "terraform_approval": {
        "allowed_workspace_phases": ["bootstrap", "core", "public", "clients"],
        "allowed_expected_actions": ["apply"],
        "required_fields": [
            "workflow_run_id",
            "head_sha",
            "workspace_phase",
            "plan_hash",
            "allowed_targets",
            "expected_action",
            "policy_decision",
            "rollback_note",
        ],
        "approved_policy_decisions": ["approved"],
        "denied_target_globs": ["google_service_account_key.*", "**/*service-account-key*"],
    },
}


def base_event():
    return {
        "head_sha": "abc123",
        "base_sha": "base123",
        "repository": "merglbot-public/docs",
        "changed_files": ["REPOSITORY_MAP.md", "ci-audit/2026-05-01/inventory.md"],
        "review_receipt": {
            "provider": "Merglbot PR Assistant",
            "head_sha": "abc123",
            "status": "approved_for_closeout",
        },
        "required_checks": [{"name": "docs-smoke", "conclusion": "success"}],
        "docs_obligation": "same_pr",
    }


class FinalMergeReadinessTest(unittest.TestCase):
    def test_docs_only_passes_with_current_head_review(self):
        receipt = evaluate_final_merge_readiness(base_event(), MANIFEST)

        self.assertEqual(receipt["decision"], DECISION_ALLOW)
        self.assertEqual(receipt["risk_class"], "docs_only")
        self.assertEqual(receipt["head_sha"], "abc123")
        self.assertEqual(receipt["changed_files_summary"]["count"], 2)
        self.assertNotIn("changed_files", receipt)
        self.assertIn("candidate_tree_sha", receipt)

    def test_stale_review_fails(self):
        event = base_event()
        event["review_receipt"]["head_sha"] = "old123"

        receipt = evaluate_final_merge_readiness(event, MANIFEST)

        self.assertEqual(receipt["decision"], DECISION_BLOCK)
        self.assertIn("Merglbot review receipt does not match current head SHA.", receipt["reasons"])

    def test_workflow_change_requires_elevated_policy(self):
        event = base_event()
        event["changed_files"] = [".github/workflows/deploy.yml"]

        receipt = evaluate_final_merge_readiness(event, MANIFEST)

        self.assertEqual(receipt["decision"], DECISION_HUMAN_REQUIRED)
        self.assertEqual(receipt["risk_class"], "workflow")

    def test_out_of_scope_repository_blocks_policy_evaluation(self):
        event = base_event()
        event["repository"] = "external-org/private-repo"

        receipt = evaluate_final_merge_readiness(event, MANIFEST)

        self.assertEqual(receipt["decision"], DECISION_BLOCK)
        self.assertFalse(receipt["repo_scope"]["accepted"])
        self.assertIn("Repository owner is explicitly excluded by policy manifest.", receipt["reasons"])

    def test_receipt_does_not_echo_denied_changed_file_paths(self):
        event = base_event()
        event["changed_files"] = ["config/service-account-prod.json"]

        receipt = evaluate_final_merge_readiness(event, MANIFEST)
        serialized = str(receipt)

        self.assertEqual(receipt["risk_class"], "security_sensitive")
        self.assertEqual(receipt["changed_files_summary"]["count"], 1)
        self.assertNotIn("service-account-prod.json", serialized)

    def test_ai_data_policy_scope_requires_policy_check(self):
        event = base_event()
        event["changed_files"] = ["MERGLBOT_AI_DATA_WORKFLOW_POLICY.md"]

        receipt = evaluate_final_merge_readiness(event, MANIFEST)

        self.assertEqual(receipt["decision"], DECISION_BLOCK)
        self.assertTrue(receipt["ai_data_policy_check"]["required"])
        self.assertIn("AI data policy check is required for this PR scope.", receipt["reasons"])

        event["ai_data_policy_check"] = "passed"
        receipt = evaluate_final_merge_readiness(event, MANIFEST)
        self.assertEqual(receipt["decision"], DECISION_ALLOW)

    def test_terraform_approval_requires_plan_hash(self):
        event = {
            "workflow_run_id": "12345",
            "head_sha": "abc123",
            "workspace_phase": "core",
            "plan_hash": "",
            "allowed_targets": [],
            "expected_action": "apply",
            "policy_decision": "approved",
            "rollback_note": "Rollback by reverting this commit and re-running apply.",
        }

        receipt = evaluate_terraform_approval(event, MANIFEST)

        self.assertEqual(receipt["decision"], DECISION_BLOCK)
        self.assertTrue(any("plan_hash" in reason or "plan hash" in reason.lower() for reason in receipt["reasons"]))

    def test_terraform_approval_does_not_echo_denied_targets(self):
        event = {
            "workflow_run_id": "12345",
            "head_sha": "abc123",
            "workspace_phase": "core",
            "plan_hash": "sha256:" + ("b" * 64),
            "allowed_targets": ["google_service_account_key.prod"],
            "expected_action": "apply",
            "policy_decision": "approved",
            "rollback_note": "Rollback by reverting this commit and re-running apply.",
        }

        receipt = evaluate_terraform_approval(event, MANIFEST)

        self.assertEqual(receipt["decision"], DECISION_BLOCK)
        self.assertEqual(receipt["allowed_targets_summary"]["count"], 1)
        self.assertNotIn("google_service_account_key.prod", str(receipt))

    def test_terraform_approval_passes_with_exact_policy_receipt(self):
        event = {
            "workflow_run_id": "12345",
            "head_sha": "abc123",
            "workspace_phase": "clients",
            "plan_hash": "sha256:" + ("a" * 64),
            "allowed_targets": ["google_cloud_run_v2_job.extractor"],
            "expected_action": "apply",
            "policy_decision": "approved",
            "rollback_note": "Disable scheduler and revert the PR if apply has runtime impact.",
        }

        receipt = evaluate_terraform_approval(event, MANIFEST)

        self.assertEqual(receipt["decision"], DECISION_ALLOW)
        self.assertEqual(receipt["workspace_phase"], "clients")
        self.assertEqual(receipt["allowed_targets_summary"]["count"], 1)
        self.assertNotIn("allowed_targets", receipt)


if __name__ == "__main__":
    unittest.main()
