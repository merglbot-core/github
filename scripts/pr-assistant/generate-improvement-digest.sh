#!/usr/bin/env bash
# Purpose: Generate a daily PR Assistant improvement digest (cross-repo metrics) from GitHub issue comments.
#
# Usage:
#   ./scripts/pr-assistant/generate-improvement-digest.sh --days-back 7 --output docs/pr-assistant/improvement-digest/2026-01-25.md
#
# Notes:
# - Requires gh auth with access to target repos (best via ENTERPRISE_GITHUB_TOKEN in CI).
# - Never prints tokens.

set -euo pipefail

DAYS_BACK="7"
OUTPUT=""

while [ $# -gt 0 ]; do
  case "$1" in
    --days-back)
      DAYS_BACK="${2:-}"
      shift 2
      ;;
    --output)
      OUTPUT="${2:-}"
      shift 2
      ;;
    -h|--help)
      echo "Usage: $0 --days-back <int> --output <path>" >&2
      exit 0
      ;;
    *)
      echo "Unknown arg: $1" >&2
      exit 2
      ;;
  esac
done

if ! [[ "$DAYS_BACK" =~ ^[0-9]+$ ]]; then
  echo "Invalid --days-back: $DAYS_BACK (must be an integer)" >&2
  exit 2
fi

if [ -z "$OUTPUT" ]; then
  echo "--output is required" >&2
  exit 2
fi

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TARGET_REPOS_FILE="${ROOT_DIR}/scripts/pr-assistant/target-repos.txt"

if [ ! -f "$TARGET_REPOS_FILE" ]; then
  echo "Target repos file not found: $TARGET_REPOS_FILE" >&2
  exit 1
fi

SINCE_DATE="$(date -u -d "$DAYS_BACK days ago" '+%Y-%m-%dT00:00:00Z' 2>/dev/null || date -u -v-"${DAYS_BACK}"d '+%Y-%m-%dT00:00:00Z')"
NOW_UTC="$(date -u '+%Y-%m-%d %H:%M UTC')"

mkdir -p "$(dirname "$OUTPUT")"

# Collect per-repo metrics as JSON lines for later aggregation.
TMP_DIR="$(mktemp -d)"
trap '[[ -d "$TMP_DIR" ]] && rm -rf -- "$TMP_DIR"' EXIT
REPO_METRICS_JSONL="${TMP_DIR}/repo-metrics.jsonl"
COMMENT_METRICS_JSONL="${TMP_DIR}/comment-metrics.jsonl"
touch "$REPO_METRICS_JSONL" "$COMMENT_METRICS_JSONL"

extract_rule_codes_json() {
  python3 - <<'PY'
import json
import re
import sys

body = sys.stdin.read()
codes = sorted(set(re.findall(r"MERGLBOT-[A-Z]+-[0-9]{3}", body)))
print(json.dumps(codes))
PY
}

extract_verdict() {
  # Reads comment body on stdin; prints APPROVE / CHANGES_NEEDED / UNKNOWN
  python3 - <<'PY'
import re
import sys

body = sys.stdin.read()
m = re.search(r'(?im)^Verdict:\s*(.+)$', body)
if not m:
  print("UNKNOWN")
  raise SystemExit(0)
v = m.group(1).strip()
v = re.sub(r'_+', ' ', v)
v = re.sub(r'[`*]+', '', v)
v = re.sub(r'\s+', ' ', v).strip()
if re.match(r'(?i)^changes\s+needed', v):
  print("CHANGES_NEEDED")
elif re.match(r'(?i)^approve', v):
  print("APPROVE")
else:
  print("UNKNOWN")
PY
}

echo "Generating digest (since: $SINCE_DATE) ..."

while IFS= read -r repo; do
  repo="$(printf '%s' "$repo" | sed -e 's/#.*$//' -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//' )"
  [ -z "$repo" ] && continue

  # Fetch issue comments since SINCE_DATE; filter only Merglbot review comments.
  COMMENTS_JSON="$({ gh api -X GET "repos/${repo}/issues/comments" --paginate -f since="$SINCE_DATE" 2>/dev/null || echo '[]'; } | jq -s 'add // []' 2>/dev/null || echo '[]')"

  MERGLBOT_COMMENTS="$(echo "$COMMENTS_JSON" | jq -c '[.[] | select(.body? | contains("Merglbot PR Assistant"))]')"
  COUNT="$(echo "$MERGLBOT_COMMENTS" | jq 'length' 2>/dev/null || echo 0)"

  UP=0
  DOWN=0
  CHANGES_NEEDED=0
  APPROVE=0
  UNKNOWN=0

  if [ "$COUNT" -eq 0 ]; then
    jq -n \
      --arg repo "$repo" \
      --arg since "$SINCE_DATE" \
      --arg generated_at "$NOW_UTC" \
      --argjson total 0 \
      --argjson up 0 \
      --argjson down 0 \
      --argjson approve 0 \
      --argjson changes_needed 0 \
      --argjson unknown 0 \
      '{repo:$repo, since:$since, generated_at:$generated_at, total:$total, reactions:{up:$up, down:$down}, verdicts:{approve:$approve, changes_needed:$changes_needed, unknown:$unknown}}' \
      >> "$REPO_METRICS_JSONL"
    continue
  fi

  # Process each comment (reactions + lightweight parsing).
  while IFS= read -r c; do
    CID="$(echo "$c" | jq -r '.id')"
    ISSUE_URL="$(echo "$c" | jq -r '.issue_url // empty')"
    PR_NUM="$(printf '%s' "$ISSUE_URL" | awk -F'/' '{print $NF}')"
    HTML_URL="$(echo "$c" | jq -r '.html_url // empty')"
    CREATED_AT="$(echo "$c" | jq -r '.created_at // empty')"
    BODY="$(echo "$c" | jq -r '.body // ""')"

    # Reactions
    REACTIONS="$(gh api "repos/${repo}/issues/comments/${CID}/reactions" 2>/dev/null || echo '[]')"
    PLUS_ONE="$(echo "$REACTIONS" | jq '[.[] | select(.content == "+1")] | length' 2>/dev/null || echo 0)"
    MINUS_ONE="$(echo "$REACTIONS" | jq '[.[] | select(.content == "-1")] | length' 2>/dev/null || echo 0)"

    # Verdict + rules
    VERDICT="$(printf '%s' "$BODY" | extract_verdict)"
    RULE_CODES_JSON="$(printf '%s' "$BODY" | extract_rule_codes_json)"

    jq -n \
      --arg repo "$repo" \
      --argjson pr_number "${PR_NUM:-0}" \
      --arg url "$HTML_URL" \
      --arg created_at "$CREATED_AT" \
      --arg verdict "$VERDICT" \
      --argjson up "$PLUS_ONE" \
      --argjson down "$MINUS_ONE" \
      --argjson rule_codes "$RULE_CODES_JSON" \
      '{repo:$repo, pr_number:$pr_number, url:$url, created_at:$created_at, verdict:$verdict, reactions:{up:$up, down:$down}, rule_codes:$rule_codes}' \
      >> "$COMMENT_METRICS_JSONL"

    UP=$((UP + PLUS_ONE))
    DOWN=$((DOWN + MINUS_ONE))
    case "$VERDICT" in
      "APPROVE") APPROVE=$((APPROVE + 1)) ;;
      "CHANGES_NEEDED") CHANGES_NEEDED=$((CHANGES_NEEDED + 1)) ;;
      *) UNKNOWN=$((UNKNOWN + 1)) ;;
    esac
  done < <(printf '%s' "$MERGLBOT_COMMENTS" | jq -c '.[]')

  jq -n \
    --arg repo "$repo" \
    --arg since "$SINCE_DATE" \
    --arg generated_at "$NOW_UTC" \
    --argjson total "$COUNT" \
    --argjson up "$UP" \
    --argjson down "$DOWN" \
    --argjson approve "$APPROVE" \
    --argjson changes_needed "$CHANGES_NEEDED" \
    --argjson unknown "$UNKNOWN" \
    '{repo:$repo, since:$since, generated_at:$generated_at, total:$total, reactions:{up:$up, down:$down}, verdicts:{approve:$approve, changes_needed:$changes_needed, unknown:$unknown}}' \
    >> "$REPO_METRICS_JSONL"

  # Rate limit protection
  sleep 0.25
done < "$TARGET_REPOS_FILE"

# Aggregate across all repos
ALL_REPOS="$(jq -s 'map(select(.repo != null))' "$REPO_METRICS_JSONL" 2>/dev/null || echo '[]')"
ALL_COMMENTS="$(jq -s 'map(select(.repo != null))' "$COMMENT_METRICS_JSONL" 2>/dev/null || echo '[]')"

TOTAL_REVIEWS="$(echo "$ALL_REPOS" | jq '[.[].total] | add // 0' 2>/dev/null || echo 0)"
TOTAL_UP="$(echo "$ALL_REPOS" | jq '[.[].reactions.up] | add // 0' 2>/dev/null || echo 0)"
TOTAL_DOWN="$(echo "$ALL_REPOS" | jq '[.[].reactions.down] | add // 0' 2>/dev/null || echo 0)"
TOTAL_FEEDBACK=$((TOTAL_UP + TOTAL_DOWN))
if [ "$TOTAL_FEEDBACK" -gt 0 ]; then
  SATISFACTION="$(awk "BEGIN {printf \"%.1f\", $TOTAL_UP * 100 / $TOTAL_FEEDBACK}")"
else
  SATISFACTION="N/A"
fi

TOP_RULES="$(echo "$ALL_COMMENTS" | jq -r '
  [.[].rule_codes[]?]
  | map(select(type=="string"))
  | sort
  | group_by(.)
  | map({code: .[0], count: length})
  | sort_by(-.count)
  | .[:20]
  | map("\(.count)\t\(.code)")[]
' 2>/dev/null || true)"

NEGATIVE_COMMENTS="$(echo "$ALL_COMMENTS" | jq -r '
  [.[] | select((.reactions.down // 0) > 0)]
  | sort_by(.created_at)
  | reverse
  | .[:25]
  | map("\(.repo)\tPR #\(.pr_number)\t\(.verdict)\t👍\(.reactions.up) 👎\(.reactions.down)\t\(.url)")[]
' 2>/dev/null || true)"

# Write markdown
cat > "$OUTPUT" <<EOF
# 🤖 PR Assistant — Improvement Digest

Generated: $NOW_UTC  
Window: last ${DAYS_BACK} day(s) (since $SINCE_DATE)

## Global Summary

| Metric | Value |
|--------|-------|
| Total reviews | $TOTAL_REVIEWS |
| 👍 Helpful | $TOTAL_UP |
| 👎 Not helpful | $TOTAL_DOWN |
| Satisfaction | ${SATISFACTION}% |

## Per-Repo Summary

| Repo | Reviews | 👍 | 👎 | Approve | Changes needed | Unknown |
|------|---------|----|----|---------|----------------|---------|
$(echo "$ALL_REPOS" | jq -r '.[] | "| \(.repo) | \(.total) | \(.reactions.up) | \(.reactions.down) | \(.verdicts.approve) | \(.verdicts.changes_needed) | \(.verdicts.unknown) |"')

## Top MERGLBOT Rule Codes (frequency)

\`\`\`
${TOP_RULES}
\`\`\`

## Recent 👎 Review Comments (triage list)

\`\`\`
repo\tPR\tverdict\tfeedback\tcomment_url
${NEGATIVE_COMMENTS}
\`\`\`

EOF

echo "Digest written: $OUTPUT"
