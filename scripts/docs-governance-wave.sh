#!/bin/bash
# docs-governance adoption wave: opens a thin advisory wrapper PR in each target repo.
# Usage: scripts/docs-governance-wave.sh <pinned-sha> [batch-size] [--apply]
# Without --apply prints the plan only (dry-run default, keeper posture).
set -euo pipefail
PIN="${1:?usage: docs-governance-wave.sh <pinned-sha> [batch] [--apply]}"
BATCH="${2:-10}"
APPLY="${3:-}"
MANIFEST="$(dirname "$0")/docs-governance-wave-repos.txt"

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

count=0
while IFS= read -r repo; do
  [[ -z "$repo" || "$repo" == \#* ]] && continue
  (( count >= BATCH )) && { echo "batch limit $BATCH reached"; break; }
  # skip repos that already have the wrapper
  if gh api "repos/$repo/contents/.github/workflows/docs-governance.yml" >/dev/null 2>&1; then
    echo "SKIP $repo (wrapper exists)"; continue
  fi
  count=$((count+1))
  if [[ "$APPLY" != "--apply" ]]; then
    echo "PLAN  $repo"
    continue
  fi
  tmp=$(mktemp -d)
  gh repo clone "$repo" "$tmp/r" -- --depth 1 -q
  cd "$tmp/r"
  git checkout -qb ci/docs-governance-advisory
  mkdir -p .github/workflows
  printf '%s\n' "$WRAPPER" > .github/workflows/docs-governance.yml
  git add .github/workflows/docs-governance.yml
  git commit -qm "ci: add advisory docs-governance check (estate-wide docs obligation)

Thin wrapper for merglbot-core/github reusable-docs-governance (mode: advisory
- never fails; warnings only). Part of the 2026-07 docs-governance program;
flip to enforce follows after a clean soak."
  git push -q origin ci/docs-governance-advisory
  gh pr create --title "ci: advisory docs-governance check" \
    --body "Adds the estate-wide **advisory** docs-governance check (never fails builds — warnings only). Reusable workflow: \`merglbot-core/github/reusable-docs-governance.yml@${PIN}\`. Evidence routes: same-PR markdown / \`MERGLBOT_DOCS_SYNC: merglbot-public/docs#<pr>\` / \`docs-impact: none\` label + reason. Flip to enforce follows after 1–2 weeks clean soak (separate PR).

🤖 Generated with [Claude Code](https://claude.com/claude-code)" 2>&1 | tail -1
  cd - >/dev/null; rm -rf "$tmp"
  echo "OPENED $repo"
done < "$MANIFEST"
echo "done: $count repo(s) processed"
