#!/usr/bin/env bash
# Purpose: Ensure "Merglbot PR Assistant v5" is a REQUIRED status check on each
# target repo's default branch — ADDITIVELY (all existing required checks are
# preserved) and IDEMPOTENTLY (repos that already require it are skipped).
#
# Design:
#   - Wraps the canonical scripts/governance/update-branch-protection.sh, which
#     preserves every other protection setting (PR reviews, restrictions, …) and
#     NEVER changes enforce_admins. We compute the full desired context set
#     (existing ∪ v5) and pass it through, because the setter REPLACES the
#     context list with the provided --check values.
#   - Repos WITHOUT branch protection are skipped and logged (a 404 means
#     "no branch protection configured", NOT "weak" — never force-create one here).
#   - Archived/fork repos are excluded.
#
# Usage:
#   apply-v5-required-check.sh [--org ORG]... [--repo ORG/REPO]... [--branch B] [--dry-run]
#   apply-v5-required-check.sh [--org ORG]... [--repo ORG/REPO]... --apply --yes
#
# Default mode is dry-run. --apply requires --yes.
set -euo pipefail

V5_CHECK="Merglbot PR Assistant v5"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SETTER="${SCRIPT_DIR}/update-branch-protection.sh"

APPLY=false
ASSUME_YES=false
SPECIFIC_BRANCH=""
TARGET_ORGS=()
TARGET_REPOS=()

log()  { printf '%s %s\n' "[$(date +'%Y-%m-%d %H:%M:%S')]" "$*"; }
warn() { printf '%s %s\n' "[$(date +'%Y-%m-%d %H:%M:%S')] [WARN]" "$*" >&2; }
err()  { printf '%s %s\n' "[$(date +'%Y-%m-%d %H:%M:%S')] [ERROR]" "$*" >&2; }

usage() {
  cat <<'EOF'
Ensure "Merglbot PR Assistant v5" is a required status check across the fleet,
additively and idempotently, preserving all other branch-protection settings.

Usage:
  apply-v5-required-check.sh [--org ORG]... [--repo ORG/REPO]... [--branch B] [--dry-run]
  apply-v5-required-check.sh [--org ORG]... [--repo ORG/REPO]... --apply --yes

Options:
  --org ORG        Target all non-archived, non-fork repos in an org (repeatable).
  --repo ORG/REPO  Target a specific repo (repeatable).
  --branch BRANCH  Branch to protect. Default: each repo's default branch.
  --apply          Apply changes (default is dry-run). Requires --yes.
  --yes            Confirm --apply.
  -h, --help       Show help.

Notes:
  - Repos without existing branch protection are SKIPPED (logged), never created.
  - enforce_admins is never modified (owner break-glass preserved).
  - The exact check context is "Merglbot PR Assistant v5" (byte-for-byte the name
    the gate publishes).
EOF
}

need_cmd() { command -v "$1" >/dev/null 2>&1 || { err "Missing dependency: $1"; exit 1; }; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --org)    [ -z "${2:-}" ] && { err "Missing arg for $1"; exit 2; }; TARGET_ORGS+=("$2"); shift 2;;
    --repo)   [ -z "${2:-}" ] && { err "Missing arg for $1"; exit 2; }; TARGET_REPOS+=("$2"); shift 2;;
    --branch) [ -z "${2:-}" ] && { err "Missing arg for $1"; exit 2; }; SPECIFIC_BRANCH="$2"; shift 2;;
    --apply)  APPLY=true; shift;;
    --yes)    ASSUME_YES=true; shift;;
    --dry-run) APPLY=false; shift;;
    -h|--help) usage; exit 0;;
    *) err "Unknown argument: $1"; usage; exit 2;;
  esac
done

need_cmd gh; need_cmd jq
[ -x "$SETTER" ] || { err "Setter not found/executable: $SETTER"; exit 1; }

if [ "${#TARGET_ORGS[@]}" -eq 0 ] && [ "${#TARGET_REPOS[@]}" -eq 0 ]; then
  err "Specify at least one --org or --repo target"; usage; exit 2
fi
if [ "$APPLY" = true ] && [ "$ASSUME_YES" != true ]; then
  err "--apply requires --yes"; exit 2
fi

# Resolve the target repo set (non-archived, non-fork).
declare -a repos=()
if [ "${#TARGET_REPOS[@]}" -gt 0 ]; then repos+=("${TARGET_REPOS[@]}"); fi
for org in "${TARGET_ORGS[@]:-}"; do
  [ -z "$org" ] && continue
  json="$(gh repo list "$org" --limit 1000 --json name,isArchived,isFork)"
  if [ "$(printf '%s' "$json" | jq 'length')" -ge 1000 ]; then
    err "${org}: gh repo list hit 1000-limit; narrow scope (results may be truncated)"; exit 2
  fi
  while IFS= read -r name; do [ -n "$name" ] && repos+=("${org}/${name}"); done < <(
    printf '%s' "$json" | jq -r '.[] | select(.isArchived==false) | select(.isFork==false) | .name'
  )
done

# Dedup.
declare -a uniq=()
while IFS= read -r r; do [ -n "$r" ] && uniq+=("$r"); done < <(printf '%s\n' "${repos[@]}" | awk 'NF' | sort -u)
repos=("${uniq[@]}")

mode="DRY-RUN"; [ "$APPLY" = true ] && mode="APPLY"
log "Mode=${mode} | targets=${#repos[@]} | check='${V5_CHECK}'"

count_ok=0 count_added=0 count_skip_nobp=0 count_fail=0
for full in "${repos[@]}"; do
  branch="$SPECIFIC_BRANCH"
  if [ -z "$branch" ]; then
    branch="$(gh api "repos/${full}" --jq '.default_branch' 2>/dev/null || true)"
    [ -z "$branch" ] && { err "${full}: cannot resolve default branch"; count_fail=$((count_fail+1)); continue; }
  fi

  # Read existing protection. Distinguish a genuine 404 (no branch protection →
  # safe to skip) from permission / rate-limit / transient API errors (fail
  # CLOSED — never silently skip a repo we could not read, which would leave the
  # v5 gate unenforced). gh prints the error body to stdout, so we inspect the
  # exit code + the stderr detail, not emptiness.
  prot_err="$(mktemp)"
  if prot="$(gh api "repos/${full}/branches/${branch}/protection" 2>"$prot_err")"; then
    rm -f "$prot_err"
  else
    detail="$(tr '\n' ' ' < "$prot_err" 2>/dev/null)"; rm -f "$prot_err"
    if printf '%s' "$detail" | grep -qiE "HTTP 404|Not Found|Branch not protected"; then
      log "SKIP ${full}:${branch} (no branch protection — 404; not weakened, just absent)"
      count_skip_nobp=$((count_skip_nobp+1)); continue
    fi
    err "${full}:${branch}: branch-protection read FAILED (not 404 — permission/API error; failing closed): ${detail}"
    count_fail=$((count_fail+1)); continue
  fi

  # Existing contexts (new 'contexts' array OR legacy 'checks[].context').
  mapfile -t existing < <(printf '%s' "$prot" | jq -r '
    (.required_status_checks.contexts // []) as $c
    | (if ($c|length)>0 then $c else [ .required_status_checks.checks[]?.context // empty ] end)[]')

  for c in "${existing[@]:-}"; do
    if [ "$c" = "$V5_CHECK" ]; then
      log "OK   ${full}:${branch} (already requires v5)"; count_ok=$((count_ok+1)); continue 2
    fi
  done

  # Build the full desired set = existing ∪ v5, passed to the canonical setter.
  setter_args=(--repo "$full" --branch "$branch")
  for c in "${existing[@]:-}"; do [ -n "$c" ] && setter_args+=(--check "$c"); done
  setter_args+=(--check "$V5_CHECK")

  if [ "$APPLY" = true ]; then
    if "$SETTER" "${setter_args[@]}" --apply --yes >/dev/null; then
      log "ADDED ${full}:${branch} (v5 now required; ${#existing[@]} existing preserved)"; count_added=$((count_added+1))
    else
      err "${full}:${branch}: setter failed"; count_fail=$((count_fail+1))
    fi
  else
    if "$SETTER" "${setter_args[@]}" --dry-run >/dev/null; then
      log "WOULD-ADD ${full}:${branch} (existing=${#existing[@]} + v5)"; count_added=$((count_added+1))
    else
      err "${full}:${branch}: setter dry-run failed"; count_fail=$((count_fail+1))
    fi
  fi
done

log "Summary: already=${count_ok} ${mode}-add=${count_added} skip-no-bp=${count_skip_nobp} fail=${count_fail}"
[ "$count_fail" -eq 0 ] || exit 1
