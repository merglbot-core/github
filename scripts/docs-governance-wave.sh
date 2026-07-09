#!/bin/bash
# docs-governance adoption wave: opens a thin advisory wrapper PR in each target repo.
# Usage: scripts/docs-governance-wave.sh <pinned-sha> [--batch=N] [--apply]
# Dry-run by default (prints the plan); --apply executes. One bad repo never
# aborts the wave (per-repo failure tolerance); repos with the wrapper are skipped.
set -euo pipefail

PIN=""
BATCH=10
APPLY=0
for arg in "$@"; do
  case "$arg" in
    --apply) APPLY=1 ;;
    --batch=*) BATCH="${arg#--batch=}" ;;
    --batch) echo "FATAL: use --batch=N (bare --batch has no value)" >&2; exit 2 ;;
    *) if [[ -z "$PIN" ]]; then PIN="$arg"; else BATCH="$arg"; fi ;;
  esac
done
[[ -n "$PIN" ]] || { echo "usage: docs-governance-wave.sh <pinned-sha> [--batch=N] [--apply]" >&2; exit 2; }
[[ "$PIN" =~ ^[0-9a-f]{40}$ ]] || { echo "FATAL: pin must be a full 40-hex commit SHA (immutable), got: $PIN" >&2; exit 2; }
[[ "$BATCH" =~ ^[1-9][0-9]*$ ]] || { echo "FATAL: --batch must be a positive integer, got: $BATCH" >&2; exit 2; }
MANIFEST="$(dirname "$0")/docs-governance-wave-repos.txt"

# Pre-flight: the reusable workflow MUST exist at the pinned SHA (guards the
# #698-merge ordering assumption at runtime instead of by convention).
if ! gh api "repos/merglbot-core/github/contents/.github/workflows/reusable-docs-governance.yml?ref=$PIN" >/dev/null 2>&1; then
  echo "FATAL: reusable-docs-governance.yml not found at merglbot-core/github@$PIN — merge the reusable workflow first" >&2
  exit 3
fi

WRAPPER=$(cat <<YAML
name: docs-governance
on:
  pull_request:
permissions:
  contents: read
  pull-requests: read
jobs:
  docs-governance:
    uses: merglbot-core/github/.github/workflows/reusable-docs-governance.yml@${PIN}
    with:
      mode: advisory
YAML
)

process_repo() {
  local repo="$1"
  if gh api "repos/$repo/contents/.github/workflows/docs-governance.yml" >/dev/null 2>&1; then
    echo "SKIP  $repo (wrapper exists)"; return 0
  fi
  local open_pr
  if ! open_pr=$(gh pr list -R "$repo" --head "${repo%%/*}:ci/docs-governance-advisory" --state open --json number --jq length 2>/dev/null); then
    echo "SKIP  $repo (open-PR lookup failed - fail-closed, retry next run)"; return 0
  fi
  if [[ "${open_pr:-0}" != "0" ]]; then
    echo "SKIP  $repo (advisory PR already open)"; return 0
  fi
  if [[ "$APPLY" -ne 1 ]]; then
    echo "PLAN  $repo"; return 0
  fi
  local tmp; tmp=$(mktemp -d)
  (
    set -e
    gh repo clone "$repo" "$tmp/r" -- --depth 1 -q
    cd "$tmp/r"
    git config user.name "merglbot-docs-governance"
    git config user.email "platform+docs-governance@merglbot.ai"
    git checkout -qb ci/docs-governance-advisory
    mkdir -p .github/workflows
    printf '%s\n' "$WRAPPER" > .github/workflows/docs-governance.yml
    git add .github/workflows/docs-governance.yml
    git commit -qm "ci: add advisory docs-governance check (estate-wide docs obligation)

Thin wrapper for merglbot-core/github reusable-docs-governance (mode: advisory
- never fails; warnings only). Part of the 2026-07 docs-governance program;
flip to enforce follows after a clean soak."
    # dedup above guarantees no open PR on this branch; force-push clears any
    # stale remote branch left by a previously closed/failed attempt
    git push -q --force origin ci/docs-governance-advisory
    gh pr create --title "ci: advisory docs-governance check" \
      --body "Adds the estate-wide **advisory** docs-governance check (never fails builds — warnings only). Reusable workflow: \`merglbot-core/github/reusable-docs-governance.yml@${PIN}\`. Evidence routes: same-PR markdown / \`MERGLBOT_DOCS_SYNC: merglbot-public/docs#<pr>\` / \`docs-impact: none\` label + reason. Flip to enforce follows after 1–2 weeks clean soak (separate PR).

🤖 Generated with [Claude Code](https://claude.com/claude-code)" | tail -1
  ) && local rc=0 || local rc=$?
  rm -rf "$tmp"
  if [[ $rc -eq 0 ]]; then echo "OPENED $repo"; else echo "FAILED $repo (rc=$rc) — continuing"; fi
  return 0
}

count=0
while IFS= read -r repo; do
  [[ -z "$repo" || "$repo" == \#* ]] && continue
  if ! [[ "$repo" =~ ^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$ ]]; then
    echo "SKIP  $repo (invalid slug)"; continue
  fi
  if ! gh repo view "$repo" --json name >/dev/null 2>&1; then
    echo "SKIP  $repo (not accessible)"; continue
  fi
  (( count >= BATCH )) && { echo "batch limit $BATCH reached"; break; }
  count=$((count+1))
  process_repo "$repo"
done < "$MANIFEST"
echo "done: $count repo(s) processed"
