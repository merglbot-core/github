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
  # pin the quoting behaviour instead of inheriting the runner's global config: with
  # core.quotePath=true a plain `git diff --name-only` C-quotes non-ASCII names, which is
  # exactly the path check.mjs must survive (it reads the diff with -z)
  git config core.quotePath true
  echo base > base.txt; git add .; git commit -qm base
  local base; base=$(git rev-parse HEAD)
  for f in "$@"; do mkdir -p "$(dirname "$f")"; echo x > "$f"; git add -- "$f"; done
  if [ -n "${DG_TEST_BULK:-}" ]; then
    # many long impact paths: the NUL-separated name list exceeds node's default 1 MiB
    # execFileSync maxBuffer, which used to throw and degrade to the fail-open `unknown`
    node -e '
      const fs = require("fs"); fs.mkdirSync("src/bulk", { recursive: true });
      const pad = "p".repeat(110);
      for (let i = 0; i < Number(process.argv[1]); i++) fs.writeFileSync(`src/bulk/${pad}-${i}.ts`, "x\n");
    ' "$DG_TEST_BULK"
    git add src/bulk
  fi
  git commit -qm change
  local head; head=$(git rev-parse HEAD)
  # local "origin" so the fetch in check.mjs succeeds
  git remote add origin "$repo"
  BASE_SHA="$base" HEAD_SHA="$head" node -e '
    const j = {pull_request:{base:{sha:process.env.BASE_SHA},head:{sha:process.env.HEAD_SHA},
      user:{login:process.env.DG_TEST_ACTOR||"test-actor"},
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

# changed paths that plain `git diff --name-only` would C-quote or that trim() would alter
# (github-security-auditor#321): each must be classified by its real name
run_case nonascii_impact missing "src/é.ts"
run_case nonascii_md_evidence satisfied "docs/é.md" src/app.ts
run_case tab_impact missing $'src/tab\there.ts'
run_case quote_impact missing 'src/quo"te.ts'
run_case backslash_impact missing 'src/back\slash.ts'
run_case leading_space_impact missing 'src/ lead.ts'
# a trailing space is part of the name: "notes.md " is not markdown evidence
run_case trailing_space_not_md missing 'docs/notes.md ' src/app.ts
# a diff larger than the default 1 MiB buffer is still classified, not `unknown`
DG_TEST_BULK=10000 run_case bulk_diff missing

# enforce exits 1 on missing
set +e
DG_TEST_MODE=enforce DG_TEST_EXPECT_EXIT=1 run_case enforce_missing missing src/app.ts
set -e

echo "----"; echo "pass=$pass fail=$fail"
[ "$fail" -eq 0 ]
