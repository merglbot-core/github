#!/usr/bin/env bash
# Purpose: Apply infra-like branch protection defaults across repos (PR-only, approvals=0, no conversation resolution).
# Usage:
#   ./scripts/governance/apply-infra-like-branch-protection.sh --org merglbot-core --dry-run
#   ./scripts/governance/apply-infra-like-branch-protection.sh --org merglbot-public
#   ./scripts/governance/apply-infra-like-branch-protection.sh --repo merglbot-cerano/feed-generator
#
# Notes:
# - This script does NOT change required status checks (per-repo CI differs).
# - By default it skips repos/branches without existing branch protection. Use `--create-if-missing` to bootstrap a baseline.
#
# Requires: gh CLI authenticated with admin access.

set -euo pipefail

# Exit codes
readonly RC_SKIPPED=20

DRY_RUN=false
INCLUDE_ARCHIVED=false
INCLUDE_FORKS=false
INCLUDE_RELEASE_BRANCHES=false
CREATE_IF_MISSING=false

TARGET_ORGS=()
TARGET_REPOS=()
SPECIFIC_BRANCH=""

DEFAULT_ORGS=(
  merglbot-core
  merglbot-public
  merglbot-cerano
  merglbot-denatura
  merglbot-proteinaco
  merglbot-extractors
  merglbot-ruzovyslon
  merglbot-autodoplnky
  merglbot-hodinarstvibechyne
  merglbot-kiteboarding
)

usage() {
  cat <<'EOF'
Apply infra-like branch protection defaults across repos.

Defaults applied (when branch protection already exists):
- required approving reviews: 0
- require conversation resolution: disabled
- require linear history: enabled
- enforce admins: enabled

This script does NOT change required status checks.

Usage:
  apply-infra-like-branch-protection.sh [--org ORG]... [--repo ORG/REPO]... [--branch BRANCH] [--include-release-branches] [--dry-run]

Options:
  --org ORG                   Target a GitHub org (repeatable). Default: a curated merglbot-* org list.
  --repo ORG/REPO             Target a specific repo (repeatable).
  --branch BRANCH             Apply to this branch name only (overrides repo default branch).
  --include-release-branches  Also apply to existing branches matching release/*.
  --create-if-missing         Create branch protection if missing (uses best-effort required status check discovery).
  --include-archived          Include archived repositories.
  --include-forks             Include forked repositories.
  --dry-run                   Print what would change, do not write.
  -h, --help                  Show help.
EOF
}

log() { printf '%s %s\n' "[$(date +'%Y-%m-%d %H:%M:%S')]" "$*"; }
warn() { printf '%s %s\n' "[WARN]" "$*" >&2; }
err() { printf '%s %s\n' "[ERROR]" "$*" >&2; }

# URL-encode a string (needed for branch names containing / like release/v1.0)
urlencode() {
  local string="$1"
  python3 -c 'import sys, urllib.parse; print(urllib.parse.quote(sys.argv[1], safe=""))' "$string"
}

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || { err "Missing dependency: $1"; exit 1; }
}

gh_api() {
  local method=$1
  local endpoint=$2
  shift 2 || true
  gh api -X "$method" "$endpoint" "$@"
}

get_default_branch() {
  local full_name=$1
  gh api "repos/${full_name}" --jq '.default_branch'
}

branch_exists() {
  local full_name=$1
  local branch=$2
  local encoded_branch
  encoded_branch="$(urlencode "$branch")"
  gh api "repos/${full_name}/branches/${encoded_branch}" >/dev/null 2>&1
}

protection_exists() {
  local full_name=$1
  local branch=$2
  local encoded_branch
  encoded_branch="$(urlencode "$branch")"
  gh api "repos/${full_name}/branches/${encoded_branch}/protection" >/dev/null 2>&1
}

list_release_branches() {
  local full_name=$1
  gh api --paginate "repos/${full_name}/branches?per_page=100" --jq '.[] | select(.name | test("^release/")) | .name'
}

discover_min_required_check() {
  local full_name=$1
  local branch=$2
  local encoded_branch
  encoded_branch="$(urlencode "$branch")"

  # Prefer a single, always-present CI gate over a long list of checks (per-repo differs).
  local preferred=(
    "ci-gate"
    "ci"
    "lint"
    "build"
    "test"
    "pytest"
    "ruff"
    "actionlint"
    "markdown-lint"
  )

  local names
  names="$(gh api --paginate "repos/${full_name}/commits/${encoded_branch}/check-runs?per_page=100" --jq '.check_runs[].name' 2>/dev/null || true)"

  local candidate
  for candidate in "${preferred[@]}"; do
    if printf '%s\n' "$names" | grep -Fxq "$candidate"; then
      printf '%s' "$candidate"
      return 0
    fi
  done

  return 1
}

create_branch_protection() {
  local full_name=$1
  local branch=$2
  local encoded_branch
  encoded_branch="$(urlencode "$branch")"

  log "Create branch protection baseline: ${full_name}:${branch}"

  if [ "$DRY_RUN" = true ]; then
    log "  DRY RUN: would create branch protection (approvals=0, strict status checks)"
    return 0
  fi

  local required_check=""
  if required_check="$(discover_min_required_check "$full_name" "$branch")"; then
    log "  Discovered required check: ${required_check}"
  else
    warn "  ${full_name}:${branch}: no stable required check discovered (creating protection with empty required checks)"
  fi

  local contexts_json="[]"
  if [ -n "$required_check" ]; then
    contexts_json="$(python3 -c 'import json,sys; print(json.dumps(sys.argv[1:]))' "$required_check")"
  fi

  local payload
  payload="$(cat <<EOF
{
  "required_status_checks": {
    "strict": true,
    "contexts": ${contexts_json}
  },
  "enforce_admins": true,
  "required_pull_request_reviews": {
    "required_approving_review_count": 0,
    "dismiss_stale_reviews": false,
    "require_code_owner_reviews": false,
    "require_last_push_approval": false
  },
  "required_linear_history": true,
  "restrictions": null,
  "allow_force_pushes": false,
  "allow_deletions": false
}
EOF
)"

  gh_api PUT "repos/${full_name}/branches/${encoded_branch}/protection" --input - <<<"$payload" >/dev/null
}

ensure_linear_history_enabled() {
  local full_name=$1
  local branch=$2
  local encoded_branch
  encoded_branch="$(urlencode "$branch")"

  # There is no dedicated REST sub-endpoint for this setting; it must be applied via the protection PUT payload.
  # We must preserve ALL existing settings to avoid unintended side effects.
  local existing
  existing="$(gh api "repos/${full_name}/branches/${encoded_branch}/protection" 2>/dev/null)" || return 0

  local enabled
  enabled="$(printf '%s' "$existing" | jq -r '.required_linear_history.enabled // false')"
  if [ "$enabled" = "true" ]; then
    return 0
  fi

  if [ "$DRY_RUN" = true ]; then
    log "  DRY RUN: enable linear history"
    return 0
  fi

  # Preserve existing required_status_checks (may be null or object)
  local required_status_checks
  required_status_checks="$(printf '%s' "$existing" | jq '.required_status_checks')"

  # Preserve existing restrictions (transform from GET format to PUT format)
  # GET returns full objects with URLs; PUT expects simple arrays of logins/slugs
  local restrictions
  restrictions="$(printf '%s' "$existing" | jq '
    if .restrictions == null then null
    else {
      users: [.restrictions.users[]?.login // empty],
      teams: [.restrictions.teams[]?.slug // empty],
      apps: [.restrictions.apps[]?.slug // empty]
    }
    end
  ')"

  # Preserve existing allow_force_pushes, allow_deletions, block_creations, lock_branch
  # GET returns {"enabled": bool}; PUT expects bool
  local allow_force_pushes
  allow_force_pushes="$(printf '%s' "$existing" | jq '.allow_force_pushes.enabled // false')"
  local allow_deletions
  allow_deletions="$(printf '%s' "$existing" | jq '.allow_deletions.enabled // false')"
  local block_creations
  block_creations="$(printf '%s' "$existing" | jq '.block_creations.enabled // false')"
  local lock_branch
  lock_branch="$(printf '%s' "$existing" | jq '.lock_branch.enabled // false')"

  # Preserve existing PR review sub-settings (dismissal_restrictions, bypass_pull_request_allowances)
  # GET returns full objects with URLs; PUT expects simple arrays of logins/slugs
  local dismissal_restrictions
  dismissal_restrictions="$(printf '%s' "$existing" | jq '
    if .required_pull_request_reviews.dismissal_restrictions == null then null
    else {
      users: [.required_pull_request_reviews.dismissal_restrictions.users[]?.login // empty],
      teams: [.required_pull_request_reviews.dismissal_restrictions.teams[]?.slug // empty],
      apps: [.required_pull_request_reviews.dismissal_restrictions.apps[]?.slug // empty]
    }
    end
  ')"
  local bypass_pull_request_allowances
  bypass_pull_request_allowances="$(printf '%s' "$existing" | jq '
    if .required_pull_request_reviews.bypass_pull_request_allowances == null then null
    else {
      users: [.required_pull_request_reviews.bypass_pull_request_allowances.users[]?.login // empty],
      teams: [.required_pull_request_reviews.bypass_pull_request_allowances.teams[]?.slug // empty],
      apps: [.required_pull_request_reviews.bypass_pull_request_allowances.apps[]?.slug // empty]
    }
    end
  ')"

  # Build required_pull_request_reviews object with preserved sub-settings
  local pr_reviews
  pr_reviews="$(jq -n \
    --argjson dismissal "$dismissal_restrictions" \
    --argjson bypass "$bypass_pull_request_allowances" \
    '{
      required_approving_review_count: 0,
      dismiss_stale_reviews: false,
      require_code_owner_reviews: false,
      require_last_push_approval: false
    } + (if $dismissal != null then {dismissal_restrictions: $dismissal} else {} end)
      + (if $bypass != null then {bypass_pull_request_allowances: $bypass} else {} end)'
  )"

  local payload
  payload="$(cat <<EOF
{
  "required_status_checks": ${required_status_checks},
  "enforce_admins": true,
  "required_pull_request_reviews": ${pr_reviews},
  "required_linear_history": true,
  "restrictions": ${restrictions},
  "allow_force_pushes": ${allow_force_pushes},
  "allow_deletions": ${allow_deletions},
  "block_creations": ${block_creations},
  "lock_branch": ${lock_branch},
  "required_conversation_resolution": false
}
EOF
)"

  gh_api PUT "repos/${full_name}/branches/${encoded_branch}/protection" --input - <<<"$payload" >/dev/null
}

apply_to_branch() {
  local full_name=$1
  local branch=$2
  local encoded_branch
  encoded_branch="$(urlencode "$branch")"

  if ! branch_exists "$full_name" "$branch"; then
    warn "Skip ${full_name}:${branch} (branch does not exist)"
    return "$RC_SKIPPED"
  fi

  if ! protection_exists "$full_name" "$branch"; then
    if [ "$CREATE_IF_MISSING" = true ]; then
      create_branch_protection "$full_name" "$branch" || return 1
    else
      warn "Skip ${full_name}:${branch} (no branch protection found)"
      return "$RC_SKIPPED"
    fi
  fi

  log "Apply infra-like protection: ${full_name}:${branch}"

  if [ "$DRY_RUN" = true ]; then
    log "  DRY RUN: set required reviews -> 0"
    log "  DRY RUN: disable conversation resolution"
    log "  DRY RUN: enable linear history (if disabled)"
    log "  DRY RUN: enable enforce-admins"
    return 0
  fi

  # 1) Approvals = 0 (still requires PRs)
  gh_api PATCH "repos/${full_name}/branches/${encoded_branch}/protection/required_pull_request_reviews" \
    -F required_approving_review_count=0 \
    -F dismiss_stale_reviews=false \
    -F require_code_owner_reviews=false \
    -F require_last_push_approval=false >/dev/null

  # 2) Conversation resolution OFF (vibecoder-friendly)
  #    DELETE is safe when already disabled (may 404).
  gh_api DELETE "repos/${full_name}/branches/${encoded_branch}/protection/required_conversation_resolution" >/dev/null 2>&1 || true

  # 3) Enforce admins ON (no bypass)
  gh_api POST "repos/${full_name}/branches/${encoded_branch}/protection/enforce_admins" >/dev/null 2>&1 || true

  # 4) Linear history ON (best-effort; only updates if currently disabled)
  ensure_linear_history_enabled "$full_name" "$branch" || true
}

list_repos_for_org() {
  local org=$1
  local jq_filter='
    .[]
    | select(.isArchived == false)
    | select(.isFork == false)
    | .name
  '

  if [ "$INCLUDE_ARCHIVED" = true ] && [ "$INCLUDE_FORKS" = true ]; then
    jq_filter='.[] | .name'
  elif [ "$INCLUDE_ARCHIVED" = true ]; then
    jq_filter='.[] | select(.isFork == false) | .name'
  elif [ "$INCLUDE_FORKS" = true ]; then
    jq_filter='.[] | select(.isArchived == false) | .name'
  fi

  gh repo list "$org" --limit 1000 --json name,isArchived,isFork --jq "$jq_filter"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run)
      DRY_RUN=true
      shift
      ;;
    --include-archived)
      INCLUDE_ARCHIVED=true
      shift
      ;;
    --include-forks)
      INCLUDE_FORKS=true
      shift
      ;;
    --include-release-branches)
      INCLUDE_RELEASE_BRANCHES=true
      shift
      ;;
    --create-if-missing)
      CREATE_IF_MISSING=true
      shift
      ;;
    --org)
      [ -z "${2:-}" ] && { err "Missing argument for $1"; usage; exit 2; }
      TARGET_ORGS+=("$2")
      shift 2
      ;;
    --repo)
      [ -z "${2:-}" ] && { err "Missing argument for $1"; usage; exit 2; }
      TARGET_REPOS+=("$2")
      shift 2
      ;;
    --branch)
      [ -z "${2:-}" ] && { err "Missing argument for $1"; usage; exit 2; }
      SPECIFIC_BRANCH="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      err "Unknown argument: $1"
      usage
      exit 2
      ;;
  esac
done

need_cmd gh
need_cmd python3
need_cmd jq
gh auth status >/dev/null 2>&1 || { err "Not logged in to GitHub via gh. Run: gh auth login"; exit 1; }

if [ "${#TARGET_ORGS[@]}" -eq 0 ] && [ "${#TARGET_REPOS[@]}" -eq 0 ]; then
  TARGET_ORGS=("${DEFAULT_ORGS[@]}")
fi

updated=0
skipped=0
failed=0

process_repo() {
  local full_name=$1

  if ! gh api "repos/${full_name}" >/dev/null 2>&1; then
    warn "Skip ${full_name} (repo not accessible)"
    skipped=$((skipped + 1))
    return 0
  fi

  local default_branch
  if [ -n "$SPECIFIC_BRANCH" ]; then
    default_branch="$SPECIFIC_BRANCH"
  else
    default_branch="$(get_default_branch "$full_name")"
  fi

  local rc=0
  apply_to_branch "$full_name" "$default_branch" || rc=$?
  case "$rc" in
    0) updated=$((updated + 1)) ;;
    "$RC_SKIPPED") skipped=$((skipped + 1)) ;;
    *) failed=$((failed + 1)) ;;
  esac

  if [ "$INCLUDE_RELEASE_BRANCHES" = true ]; then
    while IFS= read -r rel; do
      [ -z "$rel" ] && continue
      rc=0
      apply_to_branch "$full_name" "$rel" || rc=$?
      case "$rc" in
        0) updated=$((updated + 1)) ;;
        "$RC_SKIPPED") skipped=$((skipped + 1)) ;;
        *) failed=$((failed + 1)) ;;
      esac
    done < <(list_release_branches "$full_name" || true)
  fi
}

if [ "${#TARGET_REPOS[@]}" -gt 0 ]; then
  for full_name in "${TARGET_REPOS[@]}"; do
    process_repo "$full_name"
  done
fi

if [ "${#TARGET_ORGS[@]}" -gt 0 ]; then
  for org in "${TARGET_ORGS[@]}"; do
    [ -z "$org" ] && continue
    log "Enumerating repos for org: ${org}"
    while IFS= read -r repo_name; do
      [ -z "$repo_name" ] && continue
      process_repo "${org}/${repo_name}"
    done < <(list_repos_for_org "$org" || true)
  done
fi

log "Done. updated=${updated} skipped=${skipped} failed=${failed} dry_run=${DRY_RUN}"
if [ "$failed" -gt 0 ]; then
  exit 1
fi
