#!/usr/bin/env bash
# Purpose: Deterministically update GitHub branch protection, including required status checks and selected baseline settings.
# Usage examples:
#   ./scripts/governance/update-branch-protection.sh --repo merglbot-extractors/facebook-extractor --branch main --check ci --check "gitleaks / Secret Scanning" --check "dependency-review / Dependency Review" --clear-bypass-allowances --dry-run
#   ./scripts/governance/update-branch-protection.sh --org merglbot-extractors --branch main --check ci --check "gitleaks / Secret Scanning" --check "dependency-review / Dependency Review" --clear-bypass-allowances

set -euo pipefail

DRY_RUN=false
APPLY=false
ASSUME_YES=false
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
Deterministically update GitHub branch protection required checks and bypass allowances while preserving other settings by default.

Usage:
  update-branch-protection.sh [--repo ORG/REPO]... [--org ORG]... [--branch BRANCH] [--check CONTEXT]... [--clear-bypass-allowances] [--dry-run]
  update-branch-protection.sh [--repo ORG/REPO]... [--org ORG]... [--branch BRANCH] [--check CONTEXT]... [--clear-bypass-allowances] --apply --yes

Options:
  --repo ORG/REPO             Target a specific repository (repeatable).
  --org ORG                   Target all non-archived, non-fork repositories in an org (repeatable).
  --branch BRANCH             Target branch name. Default: repository default branch.
  --check CONTEXT             Required status check context to enforce (repeatable).
  --clear-bypass-allowances   Remove all PR review bypass allowances.
  --dry-run                   Write before/after/diff artifacts without applying the change.
  --apply                     Apply the change. Default mode is dry-run.
  --yes                       Required with --apply for multi-repo or bypass-clearing changes.
  -h, --help                  Show help.

Notes:
  - The script preserves existing branch protection settings by default and only updates required checks plus optional bypass clearing.
  - The script requires existing branch protection on the target branch.
  - The script refuses to create empty required status checks.
  - Artifacts are written to tmp/agent/branch-protection/<date>/<timestamp>/ and may contain GitHub metadata (users/teams/apps).
EOF
}

log() { printf '%s %s\n' "[$(date +'%Y-%m-%d %H:%M:%S')]" "$*"; }
warn() { printf '%s %s\n' "[$(date +'%Y-%m-%d %H:%M:%S')] [WARN]" "$*" >&2; }
err() { printf '%s %s\n' "[$(date +'%Y-%m-%d %H:%M:%S')] [ERROR]" "$*" >&2; }

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
  gh api -H "Accept: application/vnd.github+json" -H "X-GitHub-Api-Version: 2022-11-28" -X "$method" "$endpoint" "$@"
}

validate_repo_full_name() {
  local full_name="$1"
  [[ "$full_name" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*$ ]]
}

sanitize_path_component() {
  printf '%s' "$1" | sed 's#[/: ]#_#g'
}

list_repos_for_org() {
  local org="$1"
  local repo_json
  local repo_count
  repo_json="$(gh repo list "$org" --limit 1000 --json name,isArchived,isFork)"
  repo_count="$(printf '%s' "$repo_json" | jq 'length')"

  if [ "$repo_count" -ge 1000 ]; then
    if [ "$APPLY" = true ]; then
      err "${org}: gh repo list reached limit 1000; refusing to --apply because results may be truncated; narrow scope with --repo or smaller org subsets"
      return 2
    fi
    warn "${org}: gh repo list reached limit 1000; verify no repos were truncated"
  fi

  printf '%s' "$repo_json" | jq -r '.[] | select(.isArchived == false) | select(.isFork == false) | .name'
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
    jq -n \
      --argjson strict "$(jq '.required_status_checks.strict // true' "$before_file")" \
      --argjson contexts "$provided_checks_json" \
      '{strict: $strict, contexts: $contexts}'
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
  local before_file="$1"
  local dismissal_json="$2"
  local bypass_json="$3"

  if jq -e '.required_pull_request_reviews == null' "$before_file" >/dev/null; then
    jq -n 'null'
    return 0
  fi

  jq -n \
    --argjson required_approving_review_count "$(jq '.required_pull_request_reviews.required_approving_review_count // 0' "$before_file")" \
    --argjson dismiss_stale_reviews "$(jq '.required_pull_request_reviews.dismiss_stale_reviews // false' "$before_file")" \
    --argjson require_code_owner_reviews "$(jq '.required_pull_request_reviews.require_code_owner_reviews // false' "$before_file")" \
    --argjson require_last_push_approval "$(jq '.required_pull_request_reviews.require_last_push_approval // false' "$before_file")" \
    --argjson dismissal "$dismissal_json" \
    --argjson bypass "$bypass_json" \
    --arg clear_bypass "$CLEAR_BYPASS_ALLOWANCES" '
      {
        required_approving_review_count: $required_approving_review_count,
        dismiss_stale_reviews: $dismiss_stale_reviews,
        require_code_owner_reviews: $require_code_owner_reviews,
        require_last_push_approval: $require_last_push_approval
      }
      + (if $dismissal != null then {dismissal_restrictions: $dismissal} else {} end)
      + (if $clear_bypass == "true" or $bypass != null then {bypass_pull_request_allowances: ($bypass // {users: [], teams: [], apps: []})} else {} end)
    '
}

ensure_tmp_gitignored() {
  if ! command -v git >/dev/null 2>&1; then
    return 0
  fi

  if ! git -C "$REPO_ROOT" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    return 0
  fi

  if ! git -C "$REPO_ROOT" check-ignore -q tmp/agent/; then
    err "tmp/agent/ is not gitignored in ${REPO_ROOT}; refusing to continue"
    exit 2
  fi
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
  local diff_file="${prefix}.diff.txt"
  local payload_file="${prefix}.payload.json"
  local before_err_file="${prefix}.before.err.txt"

  if ! get_existing_protection "$full_name" "$branch" > "$before_file" 2> "$before_err_file"; then
    err "${full_name}:${branch}: existing branch protection is required (see ${before_err_file})"
    return 1
  fi

  local checks_json
  if [ "${#CHECKS[@]}" -gt 0 ]; then
    checks_json="$(python3 -c 'import json, sys; print(json.dumps(sys.argv[1:]))' "${CHECKS[@]}")"
  else
    checks_json='[]'
  fi

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
  pr_reviews_json="$(build_pr_reviews_payload "$before_file" "$dismissal_json" "$bypass_json")"

  jq -n \
    --argjson required_status_checks "$required_status_checks_json" \
    --argjson restrictions "$restrictions_json" \
    --argjson pr_reviews "$pr_reviews_json" \
    --argjson enforce_admins "$(jq '.enforce_admins.enabled // false' "$before_file")" \
    --argjson required_linear_history "$(jq '.required_linear_history.enabled // false' "$before_file")" \
    --argjson allow_force_pushes "$(jq '.allow_force_pushes.enabled // false' "$before_file")" \
    --argjson allow_deletions "$(jq '.allow_deletions.enabled // false' "$before_file")" \
    --argjson block_creations "$(jq '.block_creations.enabled // false' "$before_file")" \
    --argjson lock_branch "$(jq '.lock_branch.enabled // false' "$before_file")" \
    --argjson required_conversation_resolution "$(jq '.required_conversation_resolution.enabled // false' "$before_file")" \
    '{
      required_status_checks: $required_status_checks,
      enforce_admins: $enforce_admins,
      required_pull_request_reviews: $pr_reviews,
      required_linear_history: $required_linear_history,
      restrictions: $restrictions,
      allow_force_pushes: $allow_force_pushes,
      allow_deletions: $allow_deletions,
      block_creations: $block_creations,
      lock_branch: $lock_branch,
      required_conversation_resolution: $required_conversation_resolution
    }' > "$payload_file"

  if [ "$DRY_RUN" = true ]; then
    cp "$payload_file" "$after_file"
    write_diff_artifact "$before_file" "$after_file" "$diff_file"
    log "DRY RUN ${full_name}:${branch} -> ${diff_file}"
    return 0
  fi

  gh_api PUT "repos/${full_name}/branches/${encoded_branch}/protection" --input "$payload_file" >/dev/null || return 1
  get_existing_protection "$full_name" "$branch" > "$after_file" || return 1
  write_diff_artifact "$before_file" "$after_file" "$diff_file" || return 1
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
    --apply)
      APPLY=true
      shift
      ;;
    --clear-bypass-allowances)
      CLEAR_BYPASS_ALLOWANCES=true
      shift
      ;;
    --dry-run)
      DRY_RUN=true
      shift
      ;;
    --yes)
      ASSUME_YES=true
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

if [ "$APPLY" = true ] && [ "$DRY_RUN" = true ]; then
  err "Use either --dry-run or --apply, not both"
  exit 2
fi

ensure_tmp_gitignored
mkdir -p "$LOG_ROOT"

declare -a resolved_repos=()
if [ "${#TARGET_REPOS[@]}" -gt 0 ]; then
  resolved_repos+=("${TARGET_REPOS[@]}")
fi

if [ "${#TARGET_ORGS[@]}" -gt 0 ]; then
  for org in "${TARGET_ORGS[@]}"; do
    if org_repos="$(list_repos_for_org "$org")"; then
      :
    else
      rc=$?
      err "${org}: unable to resolve deterministic repo list (rc=${rc})"
      exit "$rc"
    fi
    while IFS= read -r repo_name; do
      [ -z "$repo_name" ] && continue
      resolved_repos+=("${org}/${repo_name}")
    done <<< "$org_repos"
  done
fi

deduped_repos=()
while IFS= read -r repo_name; do
  [ -z "$repo_name" ] && continue
  deduped_repos+=("$repo_name")
done < <(printf '%s\n' "${resolved_repos[@]}" | awk 'NF' | sort -u)
resolved_repos=("${deduped_repos[@]}")

if [ "$APPLY" != true ]; then
  DRY_RUN=true
fi

if [ "$APPLY" = true ] && [ "$ASSUME_YES" != true ]; then
  err "--apply requires --yes"
  exit 2
fi

failures=0
for full_name in "${resolved_repos[@]}"; do
  if ! validate_repo_full_name "$full_name"; then
    err "${full_name}: invalid repo name; expected ORG/REPO"
    failures=$((failures + 1))
    continue
  fi

  branch="$SPECIFIC_BRANCH"
  if [ -z "$branch" ]; then
    if ! branch="$(get_default_branch "$full_name")"; then
      err "${full_name}: unable to resolve default branch"
      failures=$((failures + 1))
      continue
    fi
  fi

  log "TARGET ${full_name}:${branch}"
  if ! (
    set -euo pipefail
    apply_to_target "$full_name" "$branch"
  ); then
    failures=$((failures + 1))
  fi
done

log "Artifacts written to ${LOG_ROOT}"

if [ "$failures" -ne 0 ]; then
  err "Completed with ${failures} failure(s)"
  exit 1
fi
