#!/usr/bin/env python3
"""Build and validate PR Assistant v4 evidence-canary receipts.

This helper is intentionally deterministic and review-only. It validates the
committed v4 canary policy and renders the PR comment/check payload that the
canary workflow publishes on the live PR head.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import re
from typing import Any


REQUIRED_ROOT_KEYS = {
    "schema_version",
    "policy_version",
    "model_policy_version",
    "prompt_policy_version",
    "runtime_type",
    "canary_command",
    "promoted_command",
    "check_name",
    "policy_engine_check_name",
    "review_boundary",
    "closeout_mode",
    "github_active_repo_denominator",
    "gcp_merglbot_project_denominator",
    "command_ownership",
    "allowed_canary_actions",
    "forbidden_actions",
    "approval_gated_actions",
    "policy_approval_receipt",
    "receipt_required_markers",
    "safe_default_verdict",
    "safe_default_status",
    "docs_automation_boundary",
}
ALLOWED_CANARY_ACTIONS = {
    "publish_review_evidence",
    "publish_final_pr_comments",
    "publish_github_check_runs",
}
FORBIDDEN_ACTIONS_REQUIRED = {
    "merge_prs",
    "deploy_production",
    "run_terraform_apply",
    "approve_github_environment_deployments",
    "create_documentation_prs",
    "bypass_branch_protection",
    "direct_push_main",
    "use_admin_bypass",
    "log_secrets",
    "use_service_account_json_keys",
}
APPROVAL_GATES_REQUIRED = {
    "enable_canary_trigger",
    "github_app_installation_or_permission_change",
    "command_promotion",
    "terraform_plan_staging_or_production",
    "terraform_apply",
    "cloud_run_deploy",
    "production_rollout",
    "destructive_rollback_beyond_disabling_app_or_command_routing",
}
RECEIPT_MARKERS_REQUIRED = {
    "MERGLBOT_PR_ASSISTANT_V4",
    "MERGLBOT_REVIEW_RECEIPT_SCHEMA_VERSION",
    "MERGLBOT_FOLLOW_UP_ID",
    "MERGLBOT_REVIEW_HEAD_SHA",
    "MERGLBOT_REVIEW_VERDICT",
    "MERGLBOT_REVIEW_STATUS",
    "MERGLBOT_PR_CHECK_SURFACE",
    "MERGLBOT_DOCUMENTATION_OBLIGATION_STATE",
    "MERGLBOT_DOCS_FOLLOW_UP_HINT",
    "MERGLBOT_SUGGESTED_DOCS_TARGETS",
    "MERGLBOT_CLOSEOUT_MODE",
    "MERGLBOT_MODEL_POLICY_VERSION",
    "MERGLBOT_PROMPT_POLICY_VERSION",
    "MERGLBOT_REVIEW_RUN_ID",
    "MERGLBOT_RUN_ID",
    "MERGLBOT_RUNTIME_TYPE",
}
HEX_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
SAFE_REF_RE = re.compile(r"^[A-Za-z0-9._/@:-]+$")
RUN_URL_RE = re.compile(r"^https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/actions/runs/[0-9]+$")
NUMERIC_RE = re.compile(r"^[0-9]+$")


def load_json(path: pathlib.Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise SystemExit(f"missing config: {path}") from exc
    except json.JSONDecodeError as exc:
        raise SystemExit(f"invalid JSON in {path}: {exc}") from exc


def as_string_set(config: dict[str, Any], key: str) -> set[str]:
    value = config.get(key)
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise SystemExit(f"{key} must be a list of strings")
    return set(value)


def validate_config(config: dict[str, Any]) -> dict[str, Any]:
    missing = sorted(REQUIRED_ROOT_KEYS - set(config))
    if missing:
        raise SystemExit(f"config missing required keys: {', '.join(missing)}")
    if config["schema_version"] != 1:
        raise SystemExit("schema_version must be 1")
    expected_strings = {
        "runtime_type": "github_actions_evidence_canary",
        "canary_command": "@merglbot review-v4",
        "promoted_command": "@merglbot review",
        "check_name": "Merglbot PR Assistant v4",
        "policy_engine_check_name": "Final Merge Readiness",
        "review_boundary": "review_only",
        "closeout_mode": "human_merge_only",
        "safe_default_verdict": "blocked_missing_authority",
        "safe_default_status": "blocked",
    }
    for key, expected in expected_strings.items():
        if config.get(key) != expected:
            raise SystemExit(f"{key} must be {expected!r}")
    if config["github_active_repo_denominator"] != 46:
        raise SystemExit("github_active_repo_denominator must be 46")
    if config["gcp_merglbot_project_denominator"] != 31:
        raise SystemExit("gcp_merglbot_project_denominator must be 31")

    allowed = as_string_set(config, "allowed_canary_actions")
    if not allowed or not allowed <= ALLOWED_CANARY_ACTIONS:
        raise SystemExit("allowed_canary_actions contains an unsupported action")
    forbidden = as_string_set(config, "forbidden_actions")
    if not FORBIDDEN_ACTIONS_REQUIRED <= forbidden:
        missing_forbidden = sorted(FORBIDDEN_ACTIONS_REQUIRED - forbidden)
        raise SystemExit(f"forbidden_actions missing: {', '.join(missing_forbidden)}")
    gates = as_string_set(config, "approval_gated_actions")
    if not APPROVAL_GATES_REQUIRED <= gates:
        missing_gates = sorted(APPROVAL_GATES_REQUIRED - gates)
        raise SystemExit(f"approval_gated_actions missing: {', '.join(missing_gates)}")
    markers = as_string_set(config, "receipt_required_markers")
    if not RECEIPT_MARKERS_REQUIRED <= markers:
        missing_markers = sorted(RECEIPT_MARKERS_REQUIRED - markers)
        raise SystemExit(f"receipt_required_markers missing: {', '.join(missing_markers)}")

    ownership = config["command_ownership"]
    if not isinstance(ownership, dict):
        raise SystemExit("command_ownership must be an object")
    if ownership.get("canary_command_owner") != "v4":
        raise SystemExit("canary_command_owner must be v4")
    if ownership.get("promoted_command_owner") != "v3_until_policy_approved_promotion":
        raise SystemExit("promoted_command_owner must stay v3 until approved promotion")
    if ownership.get("promotion_requires_exactly_one_owner_per_active_repo") is not True:
        raise SystemExit("promotion must require exactly one command owner per active repo")

    docs_boundary = config["docs_automation_boundary"]
    if not isinstance(docs_boundary, dict):
        raise SystemExit("docs_automation_boundary must be an object")
    if docs_boundary.get("may_create_docs_prs") is not False:
        raise SystemExit("v4 evidence canary must not create docs PRs")

    approval_receipt = config["policy_approval_receipt"]
    if not isinstance(approval_receipt, dict):
        raise SystemExit("policy_approval_receipt must be an object")
    if approval_receipt.get("confidentiality") != "public_audit_marker_not_secret":
        raise SystemExit("policy_approval_receipt must be declared as a non-secret public audit marker")
    if approval_receipt.get("pr_output_value") != "sha256_only":
        raise SystemExit("policy_approval_receipt pr_output_value must be sha256_only")
    return {
        "ok": True,
        "policy_version": config["policy_version"],
        "check_name": config["check_name"],
        "review_boundary": config["review_boundary"],
        "closeout_mode": config["closeout_mode"],
        "allowed_canary_actions": sorted(allowed),
        "approval_gated_actions": sorted(gates),
    }


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def require_safe(value: str, *, label: str, sha: bool = False) -> str:
    value = (value or "").strip()
    if not value:
        raise SystemExit(f"{label} is required")
    if sha:
        if not HEX_SHA_RE.match(value):
            raise SystemExit(f"{label} must be a git SHA")
    elif not SAFE_REF_RE.match(value):
        raise SystemExit(f"{label} contains unsupported characters")
    return value


def require_run_url(value: str) -> str:
    value = (value or "").strip()
    if not RUN_URL_RE.match(value):
        raise SystemExit("run_url must be a GitHub Actions run URL")
    return value


def optional_numeric(value: str, *, label: str) -> str:
    value = (value or "").strip()
    if value and not NUMERIC_RE.match(value):
        raise SystemExit(f"{label} must be numeric")
    return value


def render_comment(args: argparse.Namespace, config: dict[str, Any]) -> str:
    repo = require_safe(args.repo, label="repo")
    pr_number = require_safe(args.pr_number, label="pr_number")
    head_sha = require_safe(args.head_sha, label="head_sha", sha=True)
    base_sha = require_safe(args.base_sha, label="base_sha", sha=True)
    run_id = require_safe(args.run_id, label="run_id")
    run_url = require_run_url(args.run_url)
    check_surface = require_safe(args.check_surface, label="check_surface")
    dispatch_source = require_safe(args.dispatch_source, label="dispatch_source")
    trigger_comment_id = optional_numeric(args.trigger_comment_id, label="trigger_comment_id")
    approval_receipt = (args.policy_approval_receipt or "").strip()
    approval_receipt_sha = sha256_text(approval_receipt) if approval_receipt else ""
    follow_up_id = f"pr-{pr_number}-v4-canary-{run_id}"
    verdict = config["safe_default_verdict"]
    status = config["safe_default_status"]
    docs_state = "unknown"
    docs_hint = "none"
    suggested_docs_targets = "[]"
    closeout_mode = config["closeout_mode"]
    boundary = config["review_boundary"]
    runtime_type = config["runtime_type"]
    model_policy = config["model_policy_version"]
    prompt_policy = config["prompt_policy_version"]

    lines = [
        "## Merglbot PR Assistant v4 Evidence Canary",
        "",
        "This canary is review-only. It validates current-head binding, the PR-visible Checks API surface, and the policy-engine receipt shape for PR Assistant v4. It intentionally does not merge, deploy, run Terraform, approve environments, or create documentation PRs.",
        "",
        "## Findings",
        "### Critical (Must Fix)",
        "- None from this evidence canary.",
        "### High Priority",
        "- None from this evidence canary.",
        "### Medium Priority",
        "- v4 GitHub App review generation is not enabled by this workflow; the canary emits blocked_missing_authority until the approved runtime is available.",
        "- The policy approval receipt is a public audit marker, not a credential; PR output stores only its SHA-256 digest.",
        "### Low Priority",
        "- None from this evidence canary.",
        "",
        "## Policy Evidence",
        f"- Repository: `{repo}`",
        f"- PR: `#{pr_number}`",
        f"- Base SHA: `{base_sha}`",
        f"- Head SHA: `{head_sha}`",
        f"- Check surface: `{check_surface}`",
        f"- Policy version: `{config['policy_version']}`",
        f"- Model policy: `{model_policy}`",
        f"- Prompt policy: `{prompt_policy}`",
        f"- Runtime type: `{runtime_type}`",
        f"- Closeout mode: `{closeout_mode}`",
        f"- Approval receipt SHA-256: `{approval_receipt_sha or 'missing'}`",
        "",
        "## SSOT Sync (Docs)",
        "None",
        "",
        "## Zaver",
        f"Verdict: {verdict}",
        f"Documentation Obligation State: {docs_state}",
        f"Docs Follow-Up Hint: {docs_hint}",
        f"Suggested Docs Targets: {suggested_docs_targets}",
        "Docs Signal Basis: review_output_only",
        "",
        "<!-- MERGLBOT_PR_ASSISTANT_V4 -->",
        "<!-- MERGLBOT_REVIEW_RECEIPT_SCHEMA_VERSION: 1 -->",
        f"<!-- MERGLBOT_REVIEW_BOUNDARY: {boundary} -->",
        f"<!-- MERGLBOT_FOLLOW_UP_ID: {follow_up_id} -->",
        f"<!-- MERGLBOT_REVIEW_HEAD_SHA: {head_sha} -->",
        f"<!-- MERGLBOT_REVIEW_VERDICT: {verdict} -->",
        f"<!-- MERGLBOT_REVIEW_STATUS: {status} -->",
        f"<!-- MERGLBOT_PR_CHECK_SURFACE: {check_surface} -->",
        f"<!-- MERGLBOT_DOCUMENTATION_OBLIGATION_STATE: {docs_state} -->",
        f"<!-- MERGLBOT_DOCS_FOLLOW_UP_HINT: {docs_hint} -->",
        f"<!-- MERGLBOT_SUGGESTED_DOCS_TARGETS: {suggested_docs_targets} -->",
        f"<!-- MERGLBOT_CLOSEOUT_MODE: {closeout_mode} -->",
        f"<!-- MERGLBOT_MODEL_POLICY_VERSION: {model_policy} -->",
        f"<!-- MERGLBOT_PROMPT_POLICY_VERSION: {prompt_policy} -->",
        f"<!-- MERGLBOT_REVIEW_RUN_ID: {run_id} -->",
        f"<!-- MERGLBOT_RUN_ID: {run_id} -->",
        f"<!-- MERGLBOT_RUN_URL: {run_url} -->",
        f"<!-- MERGLBOT_RUNTIME_TYPE: {runtime_type} -->",
        f"<!-- MERGLBOT_POLICY_APPROVAL_RECEIPT_SHA256: {approval_receipt_sha} -->",
        f"<!-- MERGLBOT_DISPATCH_SOURCE: {dispatch_source} -->",
        f"<!-- MERGLBOT_TRIGGER_COMMENT_ID: {trigger_comment_id} -->",
    ]
    return "\n".join(lines) + "\n"


def build_check_payload(args: argparse.Namespace, config: dict[str, Any]) -> dict[str, Any]:
    head_sha = require_safe(args.head_sha, label="head_sha", sha=True)
    run_id = require_safe(args.run_id, label="run_id")
    run_url = require_run_url(args.run_url)
    check_surface = require_safe(args.check_surface, label="check_surface")
    verdict = config["safe_default_verdict"]
    docs_state = "unknown"
    closeout_mode = config["closeout_mode"]
    summary = (
        f"PR Assistant v4 evidence canary observed head {head_sha}. "
        f"Verdict: {verdict}. Docs obligation: {docs_state}. "
        f"Model policy: {config['model_policy_version']}. "
        f"Prompt policy: {config['prompt_policy_version']}. "
        f"Closeout mode: {closeout_mode}. Check surface: {check_surface}."
        " The neutral check conclusion means the canary ran and published evidence;"
        " the receipt verdict remains blocked_missing_authority until the approved runtime is available."
    )
    return {
        "name": config["check_name"],
        "head_sha": head_sha,
        "status": "completed",
        "conclusion": "neutral",
        "details_url": run_url,
        "external_id": f"merglbot-pr-assistant-v4-canary-{run_id}",
        "output": {
            "title": "V4 evidence canary blocked pending approved runtime",
            "summary": summary,
        },
    }


def self_test() -> int:
    config = {
        "schema_version": 1,
        "policy_version": "pr-assistant-v4-canary-policy-2026-05-01",
        "model_policy_version": "pr-assistant-v4-model-policy-2026-05-01",
        "prompt_policy_version": "pr-assistant-v4-prompt-2026-05-01",
        "runtime_type": "github_actions_evidence_canary",
        "canary_command": "@merglbot review-v4",
        "promoted_command": "@merglbot review",
        "check_name": "Merglbot PR Assistant v4",
        "policy_engine_check_name": "Final Merge Readiness",
        "review_boundary": "review_only",
        "closeout_mode": "human_merge_only",
        "github_active_repo_denominator": 46,
        "gcp_merglbot_project_denominator": 31,
        "command_ownership": {
            "canary_command_owner": "v4",
            "promoted_command_owner": "v3_until_policy_approved_promotion",
            "promotion_requires_exactly_one_owner_per_active_repo": True,
        },
        "allowed_canary_actions": sorted(ALLOWED_CANARY_ACTIONS),
        "forbidden_actions": sorted(FORBIDDEN_ACTIONS_REQUIRED),
        "approval_gated_actions": sorted(APPROVAL_GATES_REQUIRED),
        "policy_approval_receipt": {
            "confidentiality": "public_audit_marker_not_secret",
            "workflow_input_allowed": True,
            "pr_output_value": "sha256_only",
            "description": "Non-secret canary approval marker.",
        },
        "receipt_required_markers": sorted(RECEIPT_MARKERS_REQUIRED),
        "safe_default_verdict": "blocked_missing_authority",
        "safe_default_status": "blocked",
        "docs_automation_boundary": {
            "docs_follow_up_hint_is_advisory": True,
            "suggested_docs_targets_are_advisory": True,
            "may_create_docs_prs": False,
        },
    }
    assert validate_config(config)["ok"] is True
    args = argparse.Namespace(
        repo="merglbot-core/github",
        pr_number="123",
        base_sha="0" * 40,
        head_sha="1" * 40,
        run_id="42",
        run_url="https://github.com/merglbot-core/github/actions/runs/42",
        check_surface="verified",
        dispatch_source="workflow_dispatch",
        trigger_comment_id="",
        policy_approval_receipt="approval:example",
    )
    comment = render_comment(args, config)
    for marker in RECEIPT_MARKERS_REQUIRED:
        assert marker in comment
    assert "MERGLBOT_REVIEW_BOUNDARY: review_only" in comment
    assert "MERGLBOT_CLOSEOUT_MODE: human_merge_only" in comment
    payload = build_check_payload(args, config)
    assert payload["name"] == "Merglbot PR Assistant v4"
    assert payload["conclusion"] == "neutral"
    assert payload["head_sha"] == "1" * 40
    print(json.dumps({"ok": True, "self_test": "passed"}))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config",
        default=".github/pr-assistant-v4-canary.json",
        help="Path to the v4 canary policy JSON.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("validate-config")
    subparsers.add_parser("self-test")

    comment_parser = subparsers.add_parser("render-comment")
    check_parser = subparsers.add_parser("build-check-payload")
    for subparser in (comment_parser, check_parser):
        subparser.add_argument("--repo", required=True)
        subparser.add_argument("--pr-number", required=True)
        subparser.add_argument("--base-sha", required=True)
        subparser.add_argument("--head-sha", required=True)
        subparser.add_argument("--run-id", required=True)
        subparser.add_argument("--run-url", required=True)
        subparser.add_argument("--check-surface", required=True)
        subparser.add_argument("--dispatch-source", default="workflow_dispatch")
        subparser.add_argument("--trigger-comment-id", default="")
        subparser.add_argument("--policy-approval-receipt", default="")
    comment_parser.add_argument("--output", required=True)
    check_parser.add_argument("--output", required=True)

    args = parser.parse_args()
    if args.command == "self-test":
        return self_test()

    config_path = pathlib.Path(args.config)
    config = load_json(config_path)
    validation = validate_config(config)
    if args.command == "validate-config":
        print(json.dumps(validation, sort_keys=True))
        return 0
    if args.command == "render-comment":
        pathlib.Path(args.output).write_text(render_comment(args, config), encoding="utf-8")
        return 0
    if args.command == "build-check-payload":
        pathlib.Path(args.output).write_text(
            json.dumps(build_check_payload(args, config), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return 0
    raise SystemExit(f"unsupported command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
