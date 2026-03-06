#!/usr/bin/env bash
# Purpose: Deterministically update GitHub branch protection, including required status checks and selected baseline settings.
# Usage examples:
#   ./scripts/governance/update-branch-protection.sh --repo merglbot-extractors/facebook-extractor --branch main --check ci --check "gitleaks / Secret Scanning" --check "dependency-review / Dependency Review" --clear-bypass-allowances --dry-run
#   ./scripts/governance/update-branch-protection.sh --org merglbot-extractors --branch main --check ci --check "gitleaks / Secret Scanning" --check "dependency-review / Dependency Review" --clear-bypass-allowances

set -euo pipefail

DRY_RUN=false
CLEAR_BYPASS_ALLOWANCES=false

TARGET_ORGS=()
TARGET_REPOS=()
CHECKS=()
SPECIFIC_BRANCH=""

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
RUN_DATE="$(date -u +%Y-%m-%d)"
RUN_TS="$(date -u +%Y%m%dT%H%M%SZ)"
LOG_ROOT="${REPO_ROOT}/tmp/agent/branch-protection/${RUN_DATE}/${RUN_TS}"

usage() {
  cat <<'EOF'
Deterministically update GitHub branch protection settings, enforcing a baseline configuration.

Usage:
  update-branch-protection.sh [--repo ORG/REPO]... [--org ORG]... [--branch BRANCH] [--check CONTEXT]... [--clear-bypass-allowances] [--dry-run]

Options:
  --repo ORG/REPO             Target a specific repository (repeatable).
  --org ORG                   Target all non-archived, non-fork repositories in an org (repeatable).
  --branch BRANCH             Target branch name. Default: repository default branch.
  --check CONTEXT             Required status check context to enforce (repeatable).
  --clear-bypass-allowances   Remove all PR review bypass allowances.
  --dry-run                   Write before/after/diff artifacts without applying the change.
  -h, --help                  Show help.

Notes:
  - The script enforces: approvals=0, require_code_owner_reviews=false, require_last_push_approval=false,
    required_linear_history=true, enforce_admins=true, required_conversation_resolution=false.
  - Existing restrictions, dismissal restrictions, and allow_force_pushes/allow_deletions settings are preserved.
  - The script requires existing branch protection on the target branch.
  - The script refuses to create empty required status checks.
  - Artifacts are written to tmp/agent/branch-protection/<date>/<timestamp>/.
EOF
}

log() { printf '%s %s\n' "[$(date +'%Y-%m-%d %H:%M:%S')]" "$*"; }
err() { printf '%s %s\n' "[ERROR]" "$*" >&2; }

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || { err "Missing dependency: $1"; exit 1; }
}

urlencode() {
  local string="$1"
  python3 -c 'import sys, urllib.parse; print(urllib.parse.quote(sys.argv[1], safe=""))' "$string"
}

gh_api() {
  local method="$1"
  local endpoint="$2"
  shift 2 || true
  gh api -X "$method" "$endpoint" "$@"
}

sanitize_path_component() {
  printf '%s' "$1" | sed 's#[/: ]#_#g'
}

list_repos_for_org() {
  local org="$1"
  gh repo list "$org" --limit 1000 --json name,isArchived,isFork --jq '.[] | select(.isArchived == false) | select(.isFork == false) | .name'
}

get_default_branch() {
  local full_name="$1"
  gh api "repos/${full_name}" --jq '.default_branch'
}

get_existing_protection() {
  local full_name="$1"
  local branch="$2"
  local encoded_branch
  encoded_branch="$(urlencode "$branch")"
  gh api "repos/${full_name}/branches/${encoded_branch}/protection"
}

normalize_required_status_checks() {
  local before_file="$1"
  local provided_checks_json="$2"

  if [ "$provided_checks_json" != "[]" ]; then
    jq -n --argjson contexts "$provided_checks_json" '{strict: true, contexts: $contexts}'
    return 0
  fi

  jq '
    if .required_status_checks == null then
      null
    else
      {
        strict: (.required_status_checks.strict // true),
        contexts: (
          if (.required_status_checks.contexts // []) | length > 0 then
            .required_status_checks.contexts
          else
            [ .required_status_checks.checks[]?.context // empty ]
          end
        )
      }
    end
  ' "$before_file"
}

normalize_restrictions() {
  local before_file="$1"
  jq '
    if .restrictions == null then
      null
    else
      {
        users: [.restrictions.users[]?.login // empty],
        teams: [.restrictions.teams[]?.slug // empty],
        apps: [.restrictions.apps[]?.slug // empty]
      }
    end
  ' "$before_file"
}

normalize_dismissal_restrictions() {
  local before_file="$1"
  jq '
    if .required_pull_request_reviews.dismissal_restrictions == null then
      null
    else
      {
        users: [.required_pull_request_reviews.dismissal_restrictions.users[]?.login // empty],
        teams: [.required_pull_request_reviews.dismissal_restrictions.teams[]?.slug // empty],
        apps: [.required_pull_request_reviews.dismissal_restrictions.apps[]?.slug // empty]
      }
    end
  ' "$before_file"
}

normalize_bypass_allowances() {
  local before_file="$1"

  if [ "$CLEAR_BYPASS_ALLOWANCES" = true ]; then
    jq -n '{users: [], teams: [], apps: []}'
    return 0
  fi

  jq '
    if .required_pull_request_reviews.bypass_pull_request_allowances == null then
      null
    else
      {
        users: [.required_pull_request_reviews.bypass_pull_request_allowances.users[]?.login // empty],
        teams: [.required_pull_request_reviews.bypass_pull_request_allowances.teams[]?.slug // empty],
        apps: [.required_pull_request_reviews.bypass_pull_request_allowances.apps[]?.slug // empty]
      }
    end
  ' "$before_file"
}

build_pr_reviews_payload() {
  local dismissal_json="$1"
  local bypass_json="$2"

  jq -n \
    --argjson dismissal "$dismissal_json" \
    --argjson bypass "$bypass_json" \
    --arg clear_bypass "$CLEAR_BYPASS_ALLOWANCES" '
      {
        required_approving_review_count: 0,
        dismiss_stale_reviews: false,
        require_code_owner_reviews: false,
        require_last_push_approval: false
      }
      + (if $dismissal != null then {dismissal_restrictions: $dismissal} else {} end)
      + (if $clear_bypass == "true" or $bypass != null then {bypass_pull_request_allowances: ($bypass // {users: [], teams: [], apps: []})} else {} end)
    '
}

normalize_protection_for_diff() {
  local source_file="$1"

  jq '
    def normalize_subjects(items; object_key):
      if items == null then
        []
      else
        [
          items[]?
          | if type == "object" then
              .[object_key] // .name // empty
            else
              .
            end
        ] | sort
      end;

    def normalize_actor_block(block):
      if block == null then
        null
      else
        {
          users: normalize_subjects(block.users; "login"),
          teams: normalize_subjects(block.teams; "slug"),
          apps: normalize_subjects(block.apps; "slug")
        }
      end;

    def normalize_required_status_checks:
      if .required_status_checks == null then
        null
      else
        {
          strict: (.required_status_checks.strict // true),
          contexts: (
            if (.required_status_checks.contexts? | type) == "array" then
              .required_status_checks.contexts
            else
              [ .required_status_checks.checks[]?.context // empty ]
            end | sort
          )
        }
      end;

    def normalize_bool(field):
      if .[field] == null then
        false
      elif (.[field] | type) == "object" then
        (.[field].enabled // false)
      else
        .[field]
      end;

    def normalize_pr_reviews:
      if .required_pull_request_reviews == null then
        null
      else
        .required_pull_request_reviews as $reviews
        | {
            required_approving_review_count: ($reviews.required_approving_review_count // 0),
            dismiss_stale_reviews: ($reviews.dismiss_stale_reviews // false),
            require_code_owner_reviews: ($reviews.require_code_owner_reviews // false),
            require_last_push_approval: ($reviews.require_last_push_approval // false)
          }
          + (if ($reviews.dismissal_restrictions? == null) then {} else {dismissal_restrictions: normalize_actor_block($reviews.dismissal_restrictions)} end)
          + (if ($reviews.bypass_pull_request_allowances? == null) then {} else {bypass_pull_request_allowances: normalize_actor_block($reviews.bypass_pull_request_allowances)} end)
      end;

    {
      required_status_checks: normalize_required_status_checks,
      enforce_admins: normalize_bool("enforce_admins"),
      required_pull_request_reviews: normalize_pr_reviews,
      required_linear_history: normalize_bool("required_linear_history"),
      restrictions: normalize_actor_block(.restrictions),
      allow_force_pushes: normalize_bool("allow_force_pushes"),
      allow_deletions: normalize_bool("allow_deletions"),
      block_creations: normalize_bool("block_creations"),
      lock_branch: normalize_bool("lock_branch"),
      required_conversation_resolution: normalize_bool("required_conversation_resolution")
    }
  ' "$source_file"
}

write_diff_artifact() {
  local before_file="$1"
  local after_file="$2"
  local diff_file="$3"

  if diff -u <(normalize_protection_for_diff "$before_file" | jq -S .) <(normalize_protection_for_diff "$after_file" | jq -S .) > "$diff_file"; then
    :
  else
    local diff_rc=$?
    if [ "$diff_rc" -ne 1 ]; then
      return "$diff_rc"
    fi
  fi
}

apply_to_target() {
  local full_name="$1"
  local branch="$2"
  local encoded_branch
  encoded_branch="$(urlencode "$branch")"

  local repo_token
  repo_token="$(sanitize_path_component "$full_name")"
  local branch_token
  branch_token="$(sanitize_path_component "$branch")"
  local prefix="${LOG_ROOT}/${repo_token}__${branch_token}"
  local before_file="${prefix}.before.json"
  local after_file="${prefix}.after.json"
  local diff_file="${prefix}.diff.json"
  local payload_file="${prefix}.payload.json"

  mkdir -p "$LOG_ROOT"

  if ! get_existing_protection "$full_name" "$branch" > "$before_file" 2>/dev/null; then
    err "${full_name}:${branch}: existing branch protection is required"
    return 1
  fi

  local checks_json
  checks_json="$(python3 -c 'import json, sys; print(json.dumps(sys.argv[1:]))' "${CHECKS[@]}")"

  local required_status_checks_json
  required_status_checks_json="$(normalize_required_status_checks "$before_file" "$checks_json")"
  if [ "$(printf '%s' "$required_status_checks_json" | jq -r 'if . == null then "null" else (.contexts | length | tostring) end')" = "null" ]; then
    err "${full_name}:${branch}: required status checks would become null; pass --check or configure checks first"
    return 1
  fi
  if [ "$(printf '%s' "$required_status_checks_json" | jq -r '.contexts | length')" = "0" ]; then
    err "${full_name}:${branch}: refusing to write empty required status checks"
    return 1
  fi

  local restrictions_json
  restrictions_json="$(normalize_restrictions "$before_file")"
  local dismissal_json
  dismissal_json="$(normalize_dismissal_restrictions "$before_file")"
  local bypass_json
  bypass_json="$(normalize_bypass_allowances "$before_file")"
  local pr_reviews_json
  pr_reviews_json="$(build_pr_reviews_payload "$dismissal_json" "$bypass_json")"

  jq -n \
    --argjson required_status_checks "$required_status_checks_json" \
    --argjson restrictions "$restrictions_json" \
    --argjson pr_reviews "$pr_reviews_json" \
    --argjson allow_force_pushes "$(jq '.allow_force_pushes.enabled // false' "$before_file")" \
    --argjson allow_deletions "$(jq '.allow_deletions.enabled // false' "$before_file")" \
    --argjson block_creations "$(jq '.block_creations.enabled // false' "$before_file")" \
    --argjson lock_branch "$(jq '.lock_branch.enabled // false' "$before_file")" \
    '{
      required_status_checks: $required_status_checks,
      enforce_admins: true,
      required_pull_request_reviews: $pr_reviews,
      required_linear_history: true,
      restrictions: $restrictions,
      allow_force_pushes: $allow_force_pushes,
      allow_deletions: $allow_deletions,
      block_creations: $block_creations,
      lock_branch: $lock_branch,
      required_conversation_resolution: false
    }' > "$payload_file"

  if [ "$DRY_RUN" = true ]; then
    cp "$payload_file" "$after_file"
    write_diff_artifact "$before_file" "$after_file" "$diff_file"
    log "DRY RUN ${full_name}:${branch} -> ${diff_file}"
    return 0
  fi

  gh_api PUT "repos/${full_name}/branches/${encoded_branch}/protection" --input "$payload_file" >/dev/null
  get_existing_protection "$full_name" "$branch" > "$after_file"
  write_diff_artifact "$before_file" "$after_file" "$diff_file"
  log "UPDATED ${full_name}:${branch} -> ${diff_file}"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --repo)
      [ -z "${2:-}" ] && { err "Missing argument for $1"; usage; exit 2; }
      TARGET_REPOS+=("$2")
      shift 2
      ;;
    --org)
      [ -z "${2:-}" ] && { err "Missing argument for $1"; usage; exit 2; }
      TARGET_ORGS+=("$2")
      shift 2
      ;;
    --branch)
      [ -z "${2:-}" ] && { err "Missing argument for $1"; usage; exit 2; }
      SPECIFIC_BRANCH="$2"
      shift 2
      ;;
    --check)
      [ -z "${2:-}" ] && { err "Missing argument for $1"; usage; exit 2; }
      CHECKS+=("$2")
      shift 2
      ;;
    --clear-bypass-allowances)
      CLEAR_BYPASS_ALLOWANCES=true
      shift
      ;;
    --dry-run)
      DRY_RUN=true
      shift
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
need_cmd jq
need_cmd python3
need_cmd diff

if [ "${#TARGET_REPOS[@]}" -eq 0 ] && [ "${#TARGET_ORGS[@]}" -eq 0 ]; then
  err "Specify at least one --repo or --org target"
  usage
  exit 2
fi

mkdir -p "$LOG_ROOT"

declare -a resolved_repos=()
if [ "${#TARGET_REPOS[@]}" -gt 0 ]; then
  resolved_repos+=("${TARGET_REPOS[@]}")
fi

if [ "${#TARGET_ORGS[@]}" -gt 0 ]; then
  for org in "${TARGET_ORGS[@]}"; do
    while IFS= read -r repo_name; do
      [ -z "$repo_name" ] && continue
      resolved_repos+=("${org}/${repo_name}")
    done < <(list_repos_for_org "$org")
  done
fi

deduped_repos=()
while IFS= read -r repo_name; do
  [ -z "$repo_name" ] && continue
  deduped_repos+=("$repo_name")
done < <(printf '%s\n' "${resolved_repos[@]}" | awk 'NF' | sort -u)
resolved_repos=("${deduped_repos[@]}")

failures=0
for full_name in "${resolved_repos[@]}"; do
  branch="$SPECIFIC_BRANCH"
  if [ -z "$branch" ]; then
    if ! branch="$(get_default_branch "$full_name")"; then
      err "${full_name}: unable to resolve default branch"
      failures=$((failures + 1))
      continue
    fi
  fi

  log "TARGET ${full_name}:${branch}"
  if ! apply_to_target "$full_name" "$branch"; then
    failures=$((failures + 1))
  fi
done

log "Artifacts written to ${LOG_ROOT}"

if [ "$failures" -ne 0 ]; then
  err "Completed with ${failures} failure(s)"
  exit 1
fi
