#!/usr/bin/env bash
# Deploy runtime-changes guard (merglbot-core/github#909), run by
# .github/workflows/reusable-deploy-runtime-changes.yml. Diffs the LAST
# SUCCESSFULLY DEPLOYED commit (not HEAD^) against the commit to deploy and
# classifies every changed path against a deny-list of non-runtime paths.
# Contract: exit 0 with exactly one decision in $GITHUB_OUTPUT (runtime, reason,
# base-sha). Every uncertainty resolves to runtime=true: a skip must be proven.
# shellcheck disable=SC2016 # markdown backticks and jq programs are literal
set -Eeuo pipefail

MAIN_PID=$$
DECIDED=0
IN_TRAP=0
BASE=""
BASE_URL=""
DEPLOY="${RC_DEPLOY_SHA:-}"
OUT="${GITHUB_OUTPUT:-/dev/stdout}"
SUMMARY="${GITHUB_STEP_SUMMARY:-/dev/null}"
CLASSIFIED=()
TOTAL=0

# One-line rendering for GITHUB_OUTPUT, notices and the summary.
one_line() {
  local v="$1"
  v="${v//$'\r'/\\r}"
  v="${v//$'\n'/\\n}"
  v="${v//$'\t'/\\t}"
  printf '%s' "$v"
}

write_summary() {
  local runtime="$1" reason="$2" label="" entry path
  if [[ ${RC_DRY_RUN:-false} == true ]]; then label=" (DRY-RUN)"; fi
  {
    printf '### Deploy runtime-changes guard%s\n\n' "$label"
    printf -- '- decision: **runtime=%s**\n' "$runtime"
    printf -- '- reason: `%s`\n' "${reason//\`/\'}"
    printf -- '- deploy sha: `%s`\n' "${DEPLOY:-unknown}"
    printf -- '- base sha: `%s` %s\n' "${BASE:-none}" "${BASE_URL:+(run: ${BASE_URL})}"
    printf -- '- changed paths: %s (first %s listed)\n\n' "$TOTAL" "${#CLASSIFIED[@]}"
    for entry in "${CLASSIFIED[@]}"; do
      path="$(one_line "${entry#*$'\t'}")"
      printf -- '- %s: `%s`\n' "${entry%%$'\t'*}" "${path//\`/\'}"
    done
  } >> "$SUMMARY"
}

# decide <true|false> <reason>: publish the one decision and stop.
decide() {
  local runtime="$1" reason msg
  reason="$(one_line "$2")"
  {
    printf 'runtime=%s\n' "$runtime"
    printf 'reason=%s\n' "$reason"
    printf 'base-sha=%s\n' "$BASE"
  } >> "$OUT"
  DECIDED=1
  echo "deploy guard: runtime=${runtime} reason=${reason} base=${BASE:-none} deploy=${DEPLOY:-unknown}"
  write_summary "$runtime" "$reason" || true
  if [[ $runtime == false ]]; then
    msg="NO-DEPLOY ${reason} (deploy ${DEPLOY}, base ${BASE:-none})"
    printf '::notice title=deploy runtime-changes::%s\n' "${msg//%/%25}"
  fi
  exit 0
}

# shellcheck disable=SC2329 # invoked by the ERR trap
on_err() {
  # Inside $(...): fail the subshell so the parent sees it (no second decision).
  if [[ $BASHPID != "$MAIN_PID" ]]; then exit 1; fi
  if (( IN_TRAP || DECIDED )); then exit 0; fi
  IN_TRAP=1
  trap - ERR
  decide true "fail-open:error:$1"
}

# shellcheck disable=SC2329 # invoked by the EXIT trap
on_exit() {
  local rc=$?
  if [[ $BASHPID != "$MAIN_PID" ]] || (( DECIDED || IN_TRAP )); then return; fi
  IN_TRAP=1
  decide true "fail-open:error:exit-${rc}"
}

trap 'on_err "$LINENO"' ERR
trap on_exit EXIT

# valid_re <ere>: 0 when bash can compile the regex (status 2 = invalid).
valid_re() {
  local rc=0
  # shellcheck disable=SC2319 # the [[ ]] status itself is the answer
  [[ "" =~ $1 ]] 2>/dev/null || rc=$?
  (( rc != 2 ))
}

matches_any() {
  local p="$1" re
  shift
  for re in "$@"; do
    if [[ $p =~ $re ]]; then return 0; fi
  done
  return 1
}

# append_lines <array> <text>: add trimmed, non-empty, non-comment lines.
append_lines() {
  local -n dst="$1"
  local line
  while IFS= read -r line || [[ -n $line ]]; do
    line="${line#"${line%%[![:space:]]*}"}"
    line="${line%"${line##*[![:space:]]}"}"
    if [[ -z $line || $line == \#* ]]; then continue; fi
    dst+=("$line")
  done <<< "$2"
}

# Newest 10 completed main runs minus the current one: id, event, head_sha,
# run-name marker ("none" when absent), html_url.
RUNS_JQ='[.workflow_runs[] | select((.id | tostring) != $cur)] | .[:10][]
  | [(.id | tostring), .event, .head_sha,
     ((.display_title // "") | (capture(" @ (?<s>[0-9a-f]{40})$").s // "none")),
     (.html_url // "")] | @tsv'
# Jobs of one run attempt: conclusion, name. A truncated page is an error.
JOBS_JQ='(if .total_count > (.jobs | length) then error("incomplete jobs page") else empty end),
  (.jobs[] | [(.conclusion // "pending"), .name] | @tsv)'

# find_base: set BASE/BASE_URL from the newest run whose deploy jobs all succeeded.
find_base() {
  local runs_json runs id event head marker url jobs_json jobs concl name matched ok
  if ! command -v gh >/dev/null || ! command -v jq >/dev/null; then
    decide true "fail-open:missing-tool:gh-or-jq"
  fi
  if ! runs_json="$(gh api "repos/${GITHUB_REPOSITORY}/actions/workflows/${WF}/runs?branch=main&status=completed&exclude_pull_requests=true&per_page=11")"; then
    decide true "fail-open:no-base"
  fi
  if ! runs="$(jq -r --arg cur "${RC_RUN_ID:-}" "$RUNS_JQ" <<< "$runs_json")"; then
    decide true "fail-open:no-base"
  fi
  while IFS=$'\t' read -r id event head marker url; do
    if [[ -z $id ]]; then continue; fi
    if ! [[ $id =~ ^[0-9]+$ && $head =~ ^[0-9a-f]{40}$ ]]; then decide true "fail-open:no-base"; fi
    if ! jobs_json="$(gh api "repos/${GITHUB_REPOSITORY}/actions/runs/${id}/jobs?filter=latest&per_page=100")"; then
      decide true "fail-open:no-base"
    fi
    if ! jobs="$(jq -r "$JOBS_JQ" <<< "$jobs_json")"; then decide true "fail-open:no-base"; fi
    matched=0
    ok=1
    while IFS=$'\t' read -r concl name; do
      if [[ -z $name ]] || ! [[ $name =~ $JOB_RE ]]; then continue; fi
      matched=$((matched + 1))
      if [[ $concl != success ]]; then ok=0; fi
    done <<< "$jobs"
    if (( matched == 0 || ok == 0 )); then continue; fi
    BASE_URL="$url"
    if [[ $marker != none ]]; then
      BASE="$marker"
    elif [[ $event == push || $event == workflow_dispatch ]]; then
      BASE="$head"
    else
      decide true "fail-open:legacy-marker"
    fi
    return 0
  done <<< "$runs"
  decide true "fail-open:no-base"
}

WORK="${RUNNER_TEMP:?RUNNER_TEMP is required}/deploy-guard"
mkdir -p "$WORK"

# 1. Explicit operator intent always deploys.
if [[ ${RC_EVENT:-} == workflow_dispatch ]]; then decide true manual; fi
if [[ ${RC_RUN_ATTEMPT:-1} != 1 ]]; then decide true rerun; fi

# 2. Inputs. Anything malformed fails open.
if ! [[ $DEPLOY =~ ^[0-9a-f]{40}$ ]]; then decide true "fail-open:bad-deploy-sha"; fi
WF="${RC_WORKFLOW_FILE:-}"
if [[ -z $WF ]]; then
  WF="${RC_WORKFLOW_REF:-}"
  WF="${WF%%@*}"
fi
WF="${WF##*/}"
if ! [[ $WF =~ ^[A-Za-z0-9._-]+\.ya?ml$ ]]; then decide true "fail-open:bad-workflow-file"; fi
JOB_RE="${RC_DEPLOY_JOB_REGEX:-^deploy\$}"

DENY=(
  '^\.github/'
  '^docs/'
  '^[^/]+\.md$'
  '^(tests?|e2e|__tests__|playwright|cypress)/'
  '^\.(gitignore|gitattributes|editorconfig|pre-commit-config\.yaml)$'
  '^LICENSE'
  '^CODEOWNERS$'
  '^\.(vscode|devcontainer|claude|codex|cursor)/'
)
FORCE=("^\\.github/workflows/${WF//./\\.}\$" '^\.github/actions/')
append_lines DENY "${RC_DENY_EXTRA:-}"
append_lines FORCE "${RC_FORCE_EXTRA:-}"
for re in "$JOB_RE" "${DENY[@]}" "${FORCE[@]}"; do
  if ! valid_re "$re"; then decide true "fail-open:invalid-regex:${re}"; fi
done

# 3. Base = last successfully deployed commit.
if [[ ${RC_DRY_RUN:-false} == true ]]; then
  # Self-test only: no API call; the base comes from base-override.
  if [[ -z ${RC_BASE_OVERRIDE:-} ]]; then decide true "fail-open:no-base"; fi
  if ! [[ $RC_BASE_OVERRIDE =~ ^[0-9a-f]{40}$ ]]; then decide true "fail-open:bad-base-override"; fi
  BASE="$RC_BASE_OVERRIDE"
  BASE_URL="base-override"
else
  find_base
fi

# 4. Relate base and deploy commit (history only; the clone has no blobs).
if [[ $BASE == "$DEPLOY" ]]; then decide false already-deployed; fi
if ! git cat-file -e "${BASE}^{commit}" 2>/dev/null; then
  decide true "fail-open:base-missing"
fi
rc=0
git merge-base --is-ancestor "$DEPLOY" "$BASE" || rc=$?
case $rc in
  0) decide false stale-trigger ;; # never roll production back to an older commit
  1) ;;
  *) decide true "fail-open:error:merge-base-${rc}" ;;
esac
rc=0
git merge-base --is-ancestor "$BASE" "$DEPLOY" || rc=$?
case $rc in
  0) ;;
  1) decide true "fail-open:diverged" ;;
  *) decide true "fail-open:error:merge-base-${rc}" ;;
esac

# 5. Classify every changed path. -z + read -d '' survives any file name;
# no pipe, so a large diff cannot SIGPIPE into a silent skip.
CHANGED="${WORK}/changed"
if ! git diff --name-only --no-renames -z "$BASE" "$DEPLOY" -- > "$CHANGED"; then
  decide true "fail-open:error:diff"
fi
if [[ ! -s $CHANGED ]]; then decide true "fail-open:empty-diff"; fi
first_runtime=""
while IFS= read -r -d '' p; do
  TOTAL=$((TOTAL + 1))
  cls="non-runtime"
  if matches_any "$p" "${FORCE[@]}"; then
    cls="runtime(forced)"
  elif ! matches_any "$p" "${DENY[@]}"; then
    cls="runtime"
  fi
  if [[ $cls == runtime* && -z $first_runtime ]]; then first_runtime="$p"; fi
  if (( ${#CLASSIFIED[@]} < 50 )); then CLASSIFIED+=("${cls}"$'\t'"${p}"); fi
done < "$CHANGED"

if [[ -n $first_runtime ]]; then decide true "runtime:${first_runtime}"; fi
decide false non-runtime-only
