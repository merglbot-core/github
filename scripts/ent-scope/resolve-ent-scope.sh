#!/usr/bin/env bash
# resolve-ent-scope.sh — dynamic ENT repository scope resolver.
#
# Reads the canonical org allowlist from
#   merglbot-public/docs/ENT_ORG_ALLOWLIST.md (§1 table, first column contains org slugs)
# then enumerates all non-archived, non-fork repositories in each allowed org via the
# GitHub REST API, applies per-repo exclusions, and prints a canonical JSON scope.
#
# Requires:
#   - gh CLI authenticated with a token that can `gh api /orgs/<org>/repos` for every
#     allowlisted org. In GitHub Actions this must be the
#     merglbot-ent-dependabot-closeout App installation token (never a PAT).
#
# Outputs (stdout): one JSON object with the shape:
#   {
#     "generated_at": "<iso8601>",
#     "allowed_orgs": [ "merglbot-*", ... ],
#     "excluded_orgs": [ "Merglevsky-cz" ],
#     "excluded_repos": [ "merglbot-core/github", ... ],
#     "repos": [ { "full_name", "org", "name", "tier", "default_branch", "archived", "fork" }, ... ]
#   }
#
# Exit codes:
#   0 — success
#   1 — missing allowlist file
#   2 — missing org table in allowlist
#   3 — github api error for one or more orgs (partial output still written to stdout)
set -euo pipefail

ALLOWLIST_FILE="${ALLOWLIST_FILE:-}"
if [[ -z "$ALLOWLIST_FILE" ]]; then
  # Resolve relative to caller's context
  for candidate in \
    "$(pwd)/ENT_ORG_ALLOWLIST.md" \
    "$(pwd)/../merglbot-public-docs/ENT_ORG_ALLOWLIST.md" \
    "$(pwd)/merglbot-public-docs/ENT_ORG_ALLOWLIST.md" \
    "$(pwd)/docs/ENT_ORG_ALLOWLIST.md"; do
    if [[ -f "$candidate" ]]; then
      ALLOWLIST_FILE="$candidate"
      break
    fi
  done
fi

if [[ -z "$ALLOWLIST_FILE" || ! -f "$ALLOWLIST_FILE" ]]; then
  echo "ERR: cannot find ENT_ORG_ALLOWLIST.md (set ALLOWLIST_FILE env or run from a checkout that contains it)" >&2
  exit 1
fi

# Parse §1 allowed orgs table (first column backtick-wrapped or bare)
allowed_orgs=()
in_section=0
while IFS= read -r line; do
  if [[ "$line" =~ ^##[[:space:]]*1\.[[:space:]]*Allowed ]]; then
    in_section=1
    continue
  fi
  if [[ "$in_section" -eq 1 && "$line" =~ ^##[[:space:]] ]]; then
    break
  fi
  if [[ "$in_section" -eq 1 && "$line" =~ ^\|[[:space:]]*\`?(merglbot-[a-z0-9_-]+)\`? ]]; then
    allowed_orgs+=("${BASH_REMATCH[1]}")
  fi
done < "$ALLOWLIST_FILE"

if [[ "${#allowed_orgs[@]}" -eq 0 ]]; then
  echo "ERR: could not extract any allowed orgs from $ALLOWLIST_FILE §1" >&2
  exit 2
fi

# Parse §3 per-repo exclusions
excluded_repos=()
in_ex=0
while IFS= read -r line; do
  if [[ "$line" =~ ^##[[:space:]]*3\.[[:space:]]*Per-repo ]]; then
    in_ex=1
    continue
  fi
  if [[ "$in_ex" -eq 1 && "$line" =~ ^##[[:space:]] ]]; then
    break
  fi
  if [[ "$in_ex" -eq 1 && "$line" =~ ^\|[[:space:]]*\`(merglbot-[a-z0-9_/-]+)\` ]]; then
    excluded_repos+=("${BASH_REMATCH[1]}")
  fi
done < "$ALLOWLIST_FILE"

tier_for_repo() {
  local org="$1"
  case "$org" in
    merglbot-milan-private) echo "personal_experimental" ;;
    *) echo "ent_production" ;;
  esac
}

generated_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)
repos_json='[]'
any_fail=0

for org in "${allowed_orgs[@]}"; do
  # Fetch all repos, filter archived + fork
  resp=$(gh api "/orgs/$org/repos?per_page=100" --paginate 2>/dev/null || true)
  if [[ -z "$resp" || "$resp" == "null" ]]; then
    echo "WARN: could not list repos for $org (org empty or api error)" >&2
    any_fail=1
    continue
  fi
  repos_json=$(jq --argjson existing "$repos_json" --arg org "$org" --arg tier "$(tier_for_repo "$org")" --argjson excluded "$(printf '%s\n' "${excluded_repos[@]:-}" | jq -R . | jq -s .)" '
    [. + $existing | .[] ]  # merge
    | unique_by(.full_name // (.org + "/" + .name))
  ' <<< "$(echo "$resp" | jq --arg org "$org" --arg tier "$(tier_for_repo "$org")" --argjson excluded "$(printf '%s\n' "${excluded_repos[@]:-}" | jq -R . | jq -s . 2>/dev/null || echo '[]')" '
    [ .[] | select(.archived == false and .fork == false) | select((.full_name // "") as $fn | ($excluded | index($fn) | not)) |
      { full_name: .full_name, org: $org, name: .name, tier: $tier, default_branch: .default_branch, archived: .archived, fork: .fork }
    ]
  ')")
done

# Sort deterministically
repos_json=$(echo "$repos_json" | jq 'sort_by(.full_name)')

# Build final JSON
jq -n \
  --arg generated_at "$generated_at" \
  --argjson allowed "$(printf '%s\n' "${allowed_orgs[@]}" | jq -R . | jq -s .)" \
  --argjson excluded_orgs '["Merglevsky-cz"]' \
  --argjson excluded_repos "$(printf '%s\n' "${excluded_repos[@]:-}" | jq -R . | jq -s . 2>/dev/null || echo '[]')" \
  --argjson repos "$repos_json" \
  '{
    generated_at: $generated_at,
    allowlist_source: "merglbot-public/docs/ENT_ORG_ALLOWLIST.md",
    allowed_orgs: $allowed,
    excluded_orgs: $excluded_orgs,
    excluded_repos: $excluded_repos,
    repos: $repos,
    repo_count: ($repos | length),
    org_counts: ($repos | group_by(.org) | map({ (.[0].org): length }) | add)
  }'

exit "$any_fail"
