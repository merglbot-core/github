#!/usr/bin/env bash
# Apply Merglbot org-level GitHub Actions baseline settings (least privilege).
#
# Motivation:
# - `org-settings-watch.yml` DETECTS drift against config/expected-org-settings.json but cannot
#   remediate it. This script is the matching remediation tool for the orgs listed in that baseline.
# - Historical trigger was SEC-P1-003 (merglbot-shared off-baseline); that org has since been
#   brought back to baseline manually, so treat this as the general-purpose remediation path.
#
# Safety:
# - Never prints tokens.
# - Requires explicit confirmation unless --yes is provided.
# - Refuses to clear a non-empty org actions allowlist (patterns_allowed) unless
#   --allow-clearing-patterns is passed: silently emptying it breaks cross-repo reusable workflows.
# - Does NOT touch 2FA enforcement, SSO, or any secret/credential value.

set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  ./scripts/audit/apply-org-settings.sh --org <org> [--config <path>] [--dry-run] [--yes]
                                        [--allow-clearing-patterns]

Options:
  --org                       GitHub organization (e.g. merglbot-shared)
  --config                    Baseline JSON config (default: config/expected-org-settings.json)
  --dry-run                   Print current vs expected; do not apply changes
  --yes                       Skip confirmation prompt (dangerous)
  --allow-clearing-patterns   Permit emptying a non-empty Actions allowlist (patterns_allowed)

Notes:
  - Requires gh CLI auth with org admin privileges.
  - This script applies:
      * Actions permissions (allowed_actions from baseline; enabled_repositories is preserved)
      * Selected-actions allowlist (github_owned_allowed / verified_allowed / patterns_allowed)
      * Default workflow permissions (read) + can_approve_pull_request_reviews
      * members_can_create_public_repositories=false
  - It does NOT change 2FA enforcement (two_factor_requirement_enabled is watch-only).
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
ALLOW_CLEARING_PATTERNS="false"

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
    --allow-clearing-patterns)
      ALLOW_CLEARING_PATTERNS="true"
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

if ! jq -e . "$CONFIG" >/dev/null 2>&1; then
  echo "::error::Config file is not valid JSON: $CONFIG"
  exit 1
fi

read_expected_string() {
  # $1 = jq path into the baseline config; fails closed if the key is absent/null/empty.
  local value
  value="$(jq -r "$1 // empty" "$CONFIG")"
  if [ -z "$value" ]; then
    echo "::error::Baseline key missing or null in $CONFIG: $1" >&2
    return 1
  fi
  printf '%s' "$value"
}

read_expected_bool() {
  # Booleans must be read verbatim: `// empty` would swallow a legitimate `false`.
  local value
  value="$(jq -r "$1" "$CONFIG")"
  case "$value" in
    true|false) printf '%s' "$value" ;;
    *)
      echo "::error::Baseline key must be true/false in $CONFIG: $1 (got: '$value')" >&2
      return 1
      ;;
  esac
}

live_field() {
  # $1 = JSON document from the API, $2 = jq path.
  # jq's `//` treats `false` as empty, so `.k // "<missing>"` would render a live `false`
  # as "<missing>" and make every post-apply assertion on a false-valued setting fail.
  if [ -z "${1:-}" ]; then
    printf '<missing>'
    return 0
  fi
  printf '%s' "$1" | jq -r "$2 | if . == null then \"<missing>\" else tostring end" 2>/dev/null ||
    printf '<missing>'
}

expected_allowed_actions="$(read_expected_string '.expected_settings.actions.allowed_actions')"
expected_default_workflow_permissions="$(read_expected_string '.expected_settings.workflow.default_workflow_permissions')"
expected_github_owned_allowed="$(read_expected_bool '.expected_settings.actions.github_owned_allowed')"
expected_verified_allowed="$(read_expected_bool '.expected_settings.actions.verified_allowed')"
expected_can_approve_pr_reviews="$(read_expected_bool '.expected_settings.workflow.can_approve_pull_request_reviews')"
expected_members_can_create_public_repos="$(read_expected_bool '.expected_settings.organization.members_can_create_public_repositories')"

# NOTE on the GitHub API split:
#   /orgs/{org}/actions/permissions           -> enabled_repositories + allowed_actions
#   /orgs/{org}/actions/permissions/selected-actions -> github_owned_allowed / verified_allowed / patterns_allowed
# Reading github_owned_allowed from the first endpoint always yields "<missing>".
actions_json="$(gh api "/orgs/${ORG}/actions/permissions" 2>/dev/null || true)"
selected_json="$(gh api "/orgs/${ORG}/actions/permissions/selected-actions" 2>/dev/null || true)"
workflow_json="$(gh api "/orgs/${ORG}/actions/permissions/workflow" 2>/dev/null || true)"
org_json="$(gh api "/orgs/${ORG}" 2>/dev/null || true)"

if [ -z "$actions_json" ] || [ "$actions_json" = "null" ]; then
  echo "::error::Failed to read /orgs/${ORG}/actions/permissions (are you an org owner/admin?)"
  exit 1
fi

current_allowed_actions="$(live_field "$actions_json" '.allowed_actions')"
# enabled_repositories is REQUIRED by PUT /orgs/{org}/actions/permissions; preserve whatever is live.
current_enabled_repositories="$(echo "$actions_json" | jq -r '.enabled_repositories // empty')"
if [ -z "$current_enabled_repositories" ]; then
  echo "::error::Could not read enabled_repositories for ${ORG}; refusing to guess a value."
  exit 1
fi

if [ -n "$selected_json" ] && [ "$selected_json" != "null" ]; then
  current_github_owned_allowed="$(live_field "$selected_json" '.github_owned_allowed')"
  current_verified_allowed="$(live_field "$selected_json" '.verified_allowed')"
  current_patterns_allowed="$(echo "$selected_json" | jq -c '.patterns_allowed // []')"
else
  # Only populated when allowed_actions == "selected".
  current_github_owned_allowed="<n/a>"
  current_verified_allowed="<n/a>"
  current_patterns_allowed="[]"
fi
current_patterns_count="$(echo "$current_patterns_allowed" | jq -r 'length')"

current_default_workflow_permissions="$(live_field "$workflow_json" '.default_workflow_permissions')"
current_can_approve_pr_reviews="$(live_field "$workflow_json" '.can_approve_pull_request_reviews')"

current_members_can_create_public_repos="$(live_field "$org_json" '.members_can_create_public_repositories')"

expected_patterns_allowed="$(jq -c '.expected_settings.actions.patterns_allowed // []' "$CONFIG")"
expected_patterns_count="$(echo "$expected_patterns_allowed" | jq -r 'length')"

echo "=== Org settings baseline (current vs expected) ==="
echo "Org: $ORG"
echo ""
echo "Actions.enabled_repositories:      $current_enabled_repositories (preserved)"
echo "Actions.allowed_actions:           $current_allowed_actions -> $expected_allowed_actions"
echo "Actions.github_owned_allowed:      $current_github_owned_allowed -> $expected_github_owned_allowed"
echo "Actions.verified_allowed:          $current_verified_allowed -> $expected_verified_allowed"
echo "Actions.patterns_allowed:          $current_patterns_allowed -> $expected_patterns_allowed"
echo "Workflow.default_workflow_perms:   $current_default_workflow_permissions -> $expected_default_workflow_permissions"
echo "Workflow.can_approve_pr_reviews:   $current_can_approve_pr_reviews -> $expected_can_approve_pr_reviews"
echo "Org.public_repo_creation_enabled:  $current_members_can_create_public_repos -> $expected_members_can_create_public_repos"
echo ""

clearing_patterns="false"
if [ "$current_patterns_count" -gt 0 ] && [ "$expected_patterns_count" -eq 0 ]; then
  clearing_patterns="true"
  echo "::warning::Applying the baseline would REMOVE ${current_patterns_count} entr(y/ies) from the ${ORG} Actions allowlist."
  echo "           Third-party/cross-repo actions still referenced by workflows would stop resolving."
  echo ""
fi

if [ "$DRY_RUN" = "true" ]; then
  echo "Dry run: no changes applied."
  exit 0
fi

if [ "$clearing_patterns" = "true" ] && [ "$ALLOW_CLEARING_PATTERNS" != "true" ]; then
  echo "::error::Refusing to clear a non-empty patterns_allowed for ${ORG}."
  echo "::error::Re-run with --allow-clearing-patterns once you have confirmed no workflow depends on those patterns."
  exit 1
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
    --arg enabled_repositories "$current_enabled_repositories" \
    --arg allowed_actions "$expected_allowed_actions" \
    '{enabled_repositories: $enabled_repositories, allowed_actions: $allowed_actions}'
)"

selected_actions_payload="$(
  jq -n \
    --argjson github_owned_allowed "$expected_github_owned_allowed" \
    --argjson verified_allowed "$expected_verified_allowed" \
    --argjson patterns_allowed "$expected_patterns_allowed" \
    '{github_owned_allowed: $github_owned_allowed, verified_allowed: $verified_allowed, patterns_allowed: $patterns_allowed}'
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
if [ "$expected_allowed_actions" = "selected" ]; then
  # selected-actions only exists while allowed_actions == "selected".
  echo "$selected_actions_payload" | gh api -X PUT "/orgs/${ORG}/actions/permissions/selected-actions" --input - >/dev/null
fi
echo "$workflow_payload" | gh api -X PUT "/orgs/${ORG}/actions/permissions/workflow" --input - >/dev/null
echo "$org_payload" | gh api -X PATCH "/orgs/${ORG}" --input - >/dev/null

echo "✅ Applied settings. Re-checking..."

post_actions_json="$(gh api "/orgs/${ORG}/actions/permissions" 2>/dev/null || true)"
post_workflow_json="$(gh api "/orgs/${ORG}/actions/permissions/workflow" 2>/dev/null || true)"
post_org_json="$(gh api "/orgs/${ORG}" 2>/dev/null || true)"

post_allowed_actions="$(live_field "$post_actions_json" '.allowed_actions')"
post_default_workflow_permissions="$(live_field "$post_workflow_json" '.default_workflow_permissions')"
post_can_approve_pr_reviews="$(live_field "$post_workflow_json" '.can_approve_pull_request_reviews')"
post_members_can_create_public_repos="$(live_field "$post_org_json" '.members_can_create_public_repositories')"

if [ "$post_allowed_actions" != "$expected_allowed_actions" ]; then
  echo "::error::allowed_actions mismatch after apply."
  exit 1
fi

if [ "$expected_allowed_actions" = "selected" ]; then
  post_selected_json="$(gh api "/orgs/${ORG}/actions/permissions/selected-actions" 2>/dev/null || true)"
  post_github_owned_allowed="$(live_field "$post_selected_json" '.github_owned_allowed')"
  post_verified_allowed="$(live_field "$post_selected_json" '.verified_allowed')"
  if [ "$post_github_owned_allowed" != "$expected_github_owned_allowed" ] ||
     [ "$post_verified_allowed" != "$expected_verified_allowed" ]; then
    echo "::error::selected-actions mismatch after apply (github_owned_allowed/verified_allowed)."
    exit 1
  fi
fi

if [ "$post_default_workflow_permissions" != "$expected_default_workflow_permissions" ]; then
  echo "::error::default_workflow_permissions mismatch after apply."
  exit 1
fi

if [ "$post_can_approve_pr_reviews" != "$expected_can_approve_pr_reviews" ]; then
  echo "::error::can_approve_pull_request_reviews mismatch after apply."
  exit 1
fi

if [ "$post_members_can_create_public_repos" != "$expected_members_can_create_public_repos" ]; then
  echo "::error::members_can_create_public_repositories mismatch after apply."
  exit 1
fi

echo "✅ Baseline applied successfully for $ORG."
