#!/usr/bin/env bash
# Apply Merglbot org-level GitHub Actions baseline settings (least privilege).
#
# Motivation:
# - SEC-P1-003: org `merglbot-shared` currently allows all actions and defaults GITHUB_TOKEN to write.
# - Keep org settings aligned with config/expected-org-settings.json (watched by org-settings-watch.yml).
#
# Safety:
# - Never prints tokens.
# - Requires explicit confirmation unless --yes is provided.

set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  ./scripts/audit/apply-org-settings.sh --org <org> [--config <path>] [--dry-run] [--yes]

Options:
  --org       GitHub organization (e.g. merglbot-shared)
  --config    Baseline JSON config (default: config/expected-org-settings.json)
  --dry-run   Print current vs expected; do not apply changes
  --yes       Skip confirmation prompt (dangerous)

Notes:
  - Requires gh CLI auth with org admin privileges.
  - This script applies:
      * Actions permissions (allowed_actions=selected; GitHub-owned + verified allowed)
      * Default workflow permissions (read)
      * members_can_create_public_repositories=false
EOF
}

require_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "::error::$1 is required but not installed."
    exit 1
  fi
}

ORG=""
CONFIG="config/expected-org-settings.json"
DRY_RUN="false"
YES="false"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --org)
      ORG="${2:-}"
      shift 2
      ;;
    --config)
      CONFIG="${2:-}"
      shift 2
      ;;
    --dry-run)
      DRY_RUN="true"
      shift
      ;;
    --yes)
      YES="true"
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "::error::Unknown arg: $1"
      usage
      exit 1
      ;;
  esac
done

if [ -z "$ORG" ]; then
  usage
  exit 1
fi

require_cmd gh
require_cmd jq

if ! gh auth status >/dev/null 2>&1; then
  echo "::error::gh is not authenticated. Run: gh auth login"
  exit 1
fi

if [ ! -f "$CONFIG" ]; then
  echo "::error::Config file not found: $CONFIG"
  exit 1
fi

expected_allowed_actions="$(jq -r '.expected_settings.actions.allowed_actions' "$CONFIG")"
expected_github_owned_allowed="$(jq -r '.expected_settings.actions.github_owned_allowed' "$CONFIG")"
expected_verified_allowed="$(jq -r '.expected_settings.actions.verified_allowed' "$CONFIG")"
expected_default_workflow_permissions="$(jq -r '.expected_settings.workflow.default_workflow_permissions' "$CONFIG")"
expected_can_approve_pr_reviews="$(jq -r '.expected_settings.workflow.can_approve_pull_request_reviews' "$CONFIG")"
expected_members_can_create_public_repos="$(jq -r '.expected_settings.organization.members_can_create_public_repositories' "$CONFIG")"

actions_json="$(gh api "/orgs/${ORG}/actions/permissions" 2>/dev/null || true)"
workflow_json="$(gh api "/orgs/${ORG}/actions/permissions/workflow" 2>/dev/null || true)"
org_json="$(gh api "/orgs/${ORG}" 2>/dev/null || true)"

if [ -z "$actions_json" ] || [ "$actions_json" = "null" ]; then
  echo "::error::Failed to read /orgs/${ORG}/actions/permissions (are you an org owner/admin?)"
  exit 1
fi

current_allowed_actions="$(echo "$actions_json" | jq -r '.allowed_actions // "<missing>"')"
current_github_owned_allowed="$(echo "$actions_json" | jq -r '.github_owned_allowed // "<missing>"')"
current_verified_allowed="$(echo "$actions_json" | jq -r '.verified_allowed // "<missing>"')"

current_default_workflow_permissions="$(echo "$workflow_json" | jq -r '.default_workflow_permissions // "<missing>"')"
current_can_approve_pr_reviews="$(echo "$workflow_json" | jq -r '.can_approve_pull_request_reviews // "<missing>"')"

current_members_can_create_public_repos="$(echo "$org_json" | jq -r '.members_can_create_public_repositories // "<missing>"')"

echo "=== Org settings baseline (current vs expected) ==="
echo "Org: $ORG"
echo ""
echo "Actions.allowed_actions:            $current_allowed_actions -> $expected_allowed_actions"
echo "Actions.github_owned_allowed:      $current_github_owned_allowed -> $expected_github_owned_allowed"
echo "Actions.verified_allowed:          $current_verified_allowed -> $expected_verified_allowed"
echo "Workflow.default_workflow_perms:   $current_default_workflow_permissions -> $expected_default_workflow_permissions"
echo "Workflow.can_approve_pr_reviews:   $current_can_approve_pr_reviews -> $expected_can_approve_pr_reviews"
echo "Org.public_repo_creation_enabled:  $current_members_can_create_public_repos -> $expected_members_can_create_public_repos"
echo ""

if [ "$DRY_RUN" = "true" ]; then
  echo "Dry run: no changes applied."
  exit 0
fi

if [ "$YES" != "true" ]; then
  echo "This will apply org-level settings for $ORG."
  read -r -p "Type APPLY to continue: " confirmation
  if [ "$confirmation" != "APPLY" ]; then
    echo "Aborted."
    exit 1
  fi
fi

actions_payload="$(
  jq -n \
    --arg allowed_actions "$expected_allowed_actions" \
    --argjson github_owned_allowed "$expected_github_owned_allowed" \
    --argjson verified_allowed "$expected_verified_allowed" \
    '{allowed_actions: $allowed_actions, github_owned_allowed: $github_owned_allowed, verified_allowed: $verified_allowed, patterns_allowed: []}'
)"

workflow_payload="$(
  jq -n \
    --arg default_workflow_permissions "$expected_default_workflow_permissions" \
    --argjson can_approve_pull_request_reviews "$expected_can_approve_pr_reviews" \
    '{default_workflow_permissions: $default_workflow_permissions, can_approve_pull_request_reviews: $can_approve_pull_request_reviews}'
)"

org_payload="$(
  jq -n \
    --argjson members_can_create_public_repositories "$expected_members_can_create_public_repos" \
    '{members_can_create_public_repositories: $members_can_create_public_repositories}'
)"

echo "$actions_payload" | gh api -X PUT "/orgs/${ORG}/actions/permissions" --input - >/dev/null
echo "$workflow_payload" | gh api -X PUT "/orgs/${ORG}/actions/permissions/workflow" --input - >/dev/null
echo "$org_payload" | gh api -X PATCH "/orgs/${ORG}" --input - >/dev/null

echo "✅ Applied settings. Re-checking..."

post_actions_json="$(gh api "/orgs/${ORG}/actions/permissions" 2>/dev/null || true)"
post_workflow_json="$(gh api "/orgs/${ORG}/actions/permissions/workflow" 2>/dev/null || true)"
post_org_json="$(gh api "/orgs/${ORG}" 2>/dev/null || true)"

post_allowed_actions="$(echo "$post_actions_json" | jq -r '.allowed_actions // "<missing>"')"
post_default_workflow_permissions="$(echo "$post_workflow_json" | jq -r '.default_workflow_permissions // "<missing>"')"
post_members_can_create_public_repos="$(echo "$post_org_json" | jq -r '.members_can_create_public_repositories // "<missing>"')"

if [ "$post_allowed_actions" != "$expected_allowed_actions" ]; then
  echo "::error::allowed_actions mismatch after apply."
  exit 1
fi

if [ "$post_default_workflow_permissions" != "$expected_default_workflow_permissions" ]; then
  echo "::error::default_workflow_permissions mismatch after apply."
  exit 1
fi

if [ "$post_members_can_create_public_repos" != "$expected_members_can_create_public_repos" ]; then
  echo "::error::members_can_create_public_repositories mismatch after apply."
  exit 1
fi

echo "✅ Baseline applied successfully for $ORG."
