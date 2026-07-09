#!/bin/bash
# Deterministic state-machine tests for actions/docs-governance/check.mjs.
# Simulates PR events + a scratch git repo; asserts DOCS_GOVERNANCE_STATE per scenario.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ACTION="$HERE/../../actions/docs-governance"
TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT
pass=0; fail=0

run_case() { # name expected_state files... (|| separated pr body/labels via env)
  local name="$1" expected="$2"; shift 2
  local repo="$TMP/$name"; mkdir -p "$repo"; cd "$repo"
  git init -q -b main; git config user.email t@t; git config user.name t
  echo base > base.txt; git add .; git commit -qm base
  local base; base=$(git rev-parse HEAD)
  for f in "$@"; do mkdir -p "$(dirname "$f")"; echo x > "$f"; git add "$f"; done
  git commit -qm change
  local head; head=$(git rev-parse HEAD)
  # local "origin" so the fetch in check.mjs succeeds
  git remote add origin "$repo"
  BASE_SHA="$base" HEAD_SHA="$head" node -e '
    const j = {pull_request:{base:{sha:process.env.BASE_SHA},head:{sha:process.env.HEAD_SHA},
      user:{login:process.env.DG_TEST_ACTOR||"milhul6"},
      body:process.env.DG_TEST_BODY||"",
      labels:(process.env.DG_TEST_LABELS||"").split("|").filter(Boolean).map(n=>({name:n}))}};
    require("fs").writeFileSync(process.argv[1], JSON.stringify(j));
  ' "$TMP/$name.event.json"
  local out state
  out=$(DG_MODE="${DG_TEST_MODE:-advisory}" DG_EVENT_PATH="$TMP/$name.event.json" \
        DG_REPO="${DG_TEST_REPO:-merglbot-core/platform}" DG_ACTION_PATH="$ACTION" \
        node "$ACTION/check.mjs") || true
  state=$(grep -oE 'DOCS_GOVERNANCE_STATE: [a-z_]+' <<<"$out" | awk '{print $2}')
  if [ "$state" = "$expected" ]; then pass=$((pass+1)); echo "PASS $name ($state)";
  else fail=$((fail+1)); echo "FAIL $name: expected $expected got $state"; fi
  cd "$HERE"
}

run_case docs_only not_required README_extra.txt
run_case md_evidence satisfied src/app.ts docs/change.md
run_case impact_missing missing src/app.ts
DG_TEST_BODY='MERGLBOT_DOCS_SYNC: merglbot-public/docs#123' run_case ssot_sync satisfied_via_ssot_sync src/app.ts
DG_TEST_BODY='DOCS_IMPACT_NONE_REASON: pure refactor, no behavior change' \
  DG_TEST_LABELS='docs-impact: none' run_case waiver satisfied_via_waiver src/app.ts
DG_TEST_LABELS='docs-impact: none' run_case waiver_no_reason missing src/app.ts
run_case test_only not_required src/foo.test.ts
DG_TEST_ACTOR='dependabot[bot]' run_case dependabot not_required package-lock.json

# enforce exits 1 on missing
set +e
DG_TEST_MODE=enforce DG_TEST_EXPECT_EXIT=1 run_case enforce_missing missing src/app.ts
set -e

echo "----"; echo "pass=$pass fail=$fail"
[ "$fail" -eq 0 ]
