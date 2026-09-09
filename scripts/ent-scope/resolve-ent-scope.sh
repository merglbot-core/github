#!/usr/bin/env bash
# resolve-ent-scope.sh — dynamic ENT repository scope resolver.
#
# Reads the canonical org allowlist from
#   merglbot-public/docs/ENT_ORG_ALLOWLIST.md (§1 table, first column contains org slugs)
# then enumerates all non-archived, non-fork repositories in each allowed org via the
# GitHub REST API, applies per-repo exclusions, and prints a canonical JSON scope.
#
# Auth (two modes):
#   - App mode (CI): set ENT_APP_CLIENT_ID + ENT_APP_PRIVATE_KEY (PEM). The script mints
#     an App JWT (openssl RS256), maps installations, and mints a PER-ORG installation
#     token for every allowlisted org. A single installation token can NEVER span orgs —
#     that was the 2026-05..07 regression: a merglbot-core-only token silently collapsed
#     the scope to core repos (the /orgs/<org>/repos call returns only what the token can
#     see, with no error).
#   - Ambient mode (local/dev): neither env set -> uses the caller's gh auth, which must
#     be able to `gh api /orgs/<org>/repos` for every allowlisted org.
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
#   0 — success (JSON on stdout)
#   1 — missing allowlist file
#   2 — missing org table in allowlist
#   3 — FAIL-CLOSED: at least one allowlisted org could not be fully resolved
#       (missing App installation, API error, or zero repos). NO JSON is written —
#       a partial scope must never reach the SSOT files.
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

# --- Working dir (per-org listings, App key) ------------------------------------
work_dir=$(mktemp -d)
trap 'rm -rf "$work_dir"' EXIT

# --- Auth: App mode (per-org installation tokens) or ambient gh auth ------------
APP_MODE=0
declare -A ORG_INSTALLATION_ID=()
_b64url() { openssl base64 -A | tr '+/' '-_' | tr -d '='; }

if [[ -n "${ENT_APP_CLIENT_ID:-}" && -n "${ENT_APP_PRIVATE_KEY:-}" ]]; then
  APP_MODE=1
  key_file="$work_dir/app-key.pem"
  (umask 077; printf '%s' "$ENT_APP_PRIVATE_KEY" > "$key_file")
  now=$(date +%s)
  jwt_header=$(printf '{"alg":"RS256","typ":"JWT"}' | _b64url)
  jwt_payload=$(printf '{"iat":%d,"exp":%d,"iss":"%s"}' "$((now - 30))" "$((now + 540))" "$ENT_APP_CLIENT_ID" | _b64url)
  jwt_sig=$(printf '%s.%s' "$jwt_header" "$jwt_payload" | openssl dgst -sha256 -sign "$key_file" -binary | _b64url)
  app_jwt="$jwt_header.$jwt_payload.$jwt_sig"
  installations=$(curl -sf \
    -H "Authorization: Bearer $app_jwt" \
    -H "Accept: application/vnd.github+json" \
    -H "X-GitHub-Api-Version: 2022-11-28" \
    "https://api.github.com/app/installations?per_page=100")
  while IFS=$'\t' read -r login inst_id; do
    ORG_INSTALLATION_ID["$login"]="$inst_id"
  done < <(echo "$installations" | jq -r '.[] | [.account.login, (.id | tostring)] | @tsv')
  echo "INFO: App mode — ${#ORG_INSTALLATION_ID[@]} installations visible" >&2
else
  echo "INFO: ambient mode — using caller's gh auth (local/dev only)" >&2
fi

org_token() {
  # Prints an installation access token for the org (App mode), or nothing (ambient).
  local org="$1"
  if [[ "$APP_MODE" -eq 0 ]]; then return 0; fi
  local inst_id="${ORG_INSTALLATION_ID[$org]:-}"
  if [[ -z "$inst_id" ]]; then
    return 1
  fi
  curl -sf -X POST \
    -H "Authorization: Bearer $app_jwt" \
    -H "Accept: application/vnd.github+json" \
    -H "X-GitHub-Api-Version: 2022-11-28" \
    "https://api.github.com/app/installations/${inst_id}/access_tokens" | jq -r '.token'
}

generated_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)
any_fail=0
fail_reasons=()

for org in "${allowed_orgs[@]}"; do
  # Per-org credential: a single installation token can never span orgs.
  if [[ "$APP_MODE" -eq 1 ]]; then
    if ! tok=$(org_token "$org") || [[ -z "$tok" || "$tok" == "null" ]]; then
      fail_reasons+=("$org: no App installation (install merglbot-ent-dependabot-closeout on this org)")
      any_fail=1
      continue
    fi
    resp=$(GH_TOKEN="$tok" gh api "/orgs/$org/repos?per_page=100" --paginate 2>/dev/null || true)
  else
    resp=$(gh api "/orgs/$org/repos?per_page=100" --paginate 2>/dev/null || true)
  fi
  # Fail-closed contract: the listing must be a NON-EMPTY JSON array.
  #  - `[]` means the credential sees nothing in the org (unauthorized tokens get
  #    200+[] for private orgs — the exact 2026-05..07 silent-collapse mode), never
  #    that the org is empty: a legitimately all-archived org still returns its
  #    repos here and is filtered in the assembly step below.
  #  - a non-array body is gh printing an HTTP error payload (404/403) to stdout.
  if [[ -z "$resp" || "$resp" == "null" || "$resp" == "[]" || "$resp" != \[* ]]; then
    fail_reasons+=("$org: repo listing empty, non-array, or api error")
    any_fail=1
    continue
  fi
  printf '%s' "$resp" > "$work_dir/org-$org.json"
done

if [[ "$any_fail" -ne 0 ]]; then
  echo "ERR: scope resolution FAILED CLOSED — refusing to emit a partial scope:" >&2
  printf '  - %s\n' "${fail_reasons[@]}" >&2
  exit 3
fi

# Assemble the final scope JSON deterministically (stdlib python3; replaces the
# former nested-jq merge, whose quoting made failures untraceable).
GENERATED_AT="$generated_at" \
WORK_DIR="$work_dir" \
ALLOWED_ORGS="$(printf '%s\n' "${allowed_orgs[@]}")" \
EXCLUDED_REPOS="$(printf '%s\n' "${excluded_repos[@]:-}")" \
python3 <<'PY'
import json, os, sys

work_dir = os.environ["WORK_DIR"]
allowed = [o for o in os.environ["ALLOWED_ORGS"].splitlines() if o]
excluded = {r for r in os.environ["EXCLUDED_REPOS"].splitlines() if r}
tiers = {"merglbot-milan-private": "personal_experimental"}

repos = {}
for org in allowed:
    raw = open(os.path.join(work_dir, f"org-{org}.json")).read()
    # `gh api --paginate` concatenates one JSON array per page; parse them all.
    decoder = json.JSONDecoder()
    idx, items = 0, []
    while idx < len(raw):
        while idx < len(raw) and raw[idx].isspace():
            idx += 1
        if idx >= len(raw):
            break
        page, end = decoder.raw_decode(raw, idx)
        if not isinstance(page, list):
            sys.stderr.write(f"ERR: {org}: repo listing page is not a JSON array — failing closed\n")
            sys.exit(3)
        items.extend(page)
        idx = end
    for r in items:
        full = r.get("full_name") or f"{org}/{r.get('name')}"
        if r.get("archived") or r.get("fork") or full in excluded:
            continue
        repos[full] = {
            "full_name": full,
            "org": org,
            "name": r.get("name"),
            "tier": tiers.get(org, "ent_production"),
            "default_branch": r.get("default_branch"),
            "archived": False,
            "fork": False,
        }

repo_list = [repos[k] for k in sorted(repos)]
org_counts = {}
for r in repo_list:
    org_counts[r["org"]] = org_counts.get(r["org"], 0) + 1

json.dump(
    {
        "generated_at": os.environ["GENERATED_AT"],
        "allowlist_source": "merglbot-public/docs/ENT_ORG_ALLOWLIST.md",
        "allowed_orgs": allowed,
        "excluded_orgs": ["Merglevsky-cz"],
        "excluded_repos": sorted(excluded),
        "repos": repo_list,
        "repo_count": len(repo_list),
        "org_counts": org_counts,
    },
    sys.stdout,
    indent=2,
    sort_keys=False,
)
sys.stdout.write("\n")
PY

exit 0
