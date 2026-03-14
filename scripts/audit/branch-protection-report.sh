#!/usr/bin/env bash
# Report branch protection settings across org repos (read-only).
#
# Goal (SEC-P1-001 / SEC-P1-002):
# - Make it easy to identify repos with:
#   * required approvals = 0
#   * missing CODEOWNER review requirement
#   * missing required status checks
#
# This script does NOT apply changes.

set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  ./scripts/audit/branch-protection-report.sh --org <org> [--output <path>] [--limit <n>]
  ./scripts/audit/branch-protection-report.sh --all-orgs [--config <path>] [--output <path>] [--limit <n>]

Options:
  --org        GitHub org to scan (e.g. merglbot-core)
  --all-orgs   Scan all orgs listed in config/expected-org-settings.json
  --config     Config JSON (default: config/expected-org-settings.json)
  --output     Output CSV path (default: reports/branch-protection.csv)
  --limit      Repo list limit per org (default: 1000)

Requires:
  - gh CLI authenticated with admin privileges to read branch protection
  - jq
EOF
}

require_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "::error::$1 is required but not installed."
    exit 1
  fi
}

ORG=""
ALL_ORGS="false"
CONFIG="config/expected-org-settings.json"
OUTPUT="reports/branch-protection.csv"
LIMIT="1000"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --org)
      ORG="${2:-}"
      shift 2
      ;;
    --all-orgs)
      ALL_ORGS="true"
      shift
      ;;
    --config)
      CONFIG="${2:-}"
      shift 2
      ;;
    --output)
      OUTPUT="${2:-}"
      shift 2
      ;;
    --limit)
      LIMIT="${2:-}"
      shift 2
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

require_cmd gh
require_cmd jq

if ! gh auth status >/dev/null 2>&1; then
  echo "::error::gh is not authenticated. Run: gh auth login"
  exit 1
fi

if [ "$ALL_ORGS" = "true" ]; then
  if [ ! -f "$CONFIG" ]; then
    echo "::error::Config file not found: $CONFIG"
    exit 1
  fi
  ORGS="$(jq -r '.organizations[]' "$CONFIG" | tr '\n' ' ')"
else
  if [ -z "$ORG" ]; then
    usage
    exit 1
  fi
  ORGS="$ORG"
fi

mkdir -p "$(dirname "$OUTPUT")"

{
  echo "org,repo,default_branch,protected,required_reviews,require_code_owner_reviews,require_conversation_resolution,required_checks_count,required_checks_strict,enforce_admins,allow_force_pushes,allow_deletions"
} > "$OUTPUT"

for org in $ORGS; do
  echo "Scanning org: $org"

  repos_json="$(gh repo list "$org" --limit "$LIMIT" --json nameWithOwner,defaultBranchRef,isArchived --jq '.')"
  repo_count="$(echo "$repos_json" | jq 'length')"
  echo "  repos: $repo_count"

  echo "$repos_json" | jq -r '.[] | select(.isArchived==false) | [.nameWithOwner, (.defaultBranchRef.name // "main")] | @tsv' \
    | while IFS=$'\t' read -r repo_full branch; do
        protection_json=""
        if protection_json="$(gh api "/repos/${repo_full}/branches/${branch}/protection" 2>/dev/null)"; then
          protected="true"
        else
          protected="false"
          protection_json="{}"
        fi

        required_reviews="$(echo "$protection_json" | jq -r '.required_pull_request_reviews.required_approving_review_count // ""')"
        require_codeowners="$(echo "$protection_json" | jq -r '.required_pull_request_reviews.require_code_owner_reviews // ""')"
        require_conversation_resolution="$(echo "$protection_json" | jq -r '.required_conversation_resolution.enabled // ""')"

        required_checks_count="$(echo "$protection_json" | jq -r '.required_status_checks.contexts | length? // ""')"
        required_checks_strict="$(echo "$protection_json" | jq -r '.required_status_checks.strict // ""')"

        enforce_admins="$(echo "$protection_json" | jq -r '.enforce_admins.enabled // ""')"
        allow_force_pushes="$(echo "$protection_json" | jq -r '.allow_force_pushes.enabled // ""')"
        allow_deletions="$(echo "$protection_json" | jq -r '.allow_deletions.enabled // ""')"

        echo "$org,$repo_full,$branch,$protected,$required_reviews,$require_codeowners,$require_conversation_resolution,$required_checks_count,$required_checks_strict,$enforce_admins,$allow_force_pushes,$allow_deletions" >> "$OUTPUT"
      done
done

echo "✅ Wrote report: $OUTPUT"
