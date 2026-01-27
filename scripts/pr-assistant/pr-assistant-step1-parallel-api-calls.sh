#!/usr/bin/env bash
# Purpose: Step 1 of merglbot-pr-assistant-v3-on-demand.yml (Parallel AI calls).
# Notes:
# - Invoked from GitHub Actions; expects PR context files (pr_title.txt, pr_diff.txt, etc.) to exist in CWD.
# - Never prints tokens.

set -euo pipefail

echo "========================================="
echo "STEP 1: PARALLEL AI ANALYSIS"
echo "========================================="

sanitize_model() {
  local raw="${1:-}"
  raw="$(printf '%s' "$raw" | tr -d '\r' | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
  case "$raw" in
    *[[:space:]]*) raw="" ;;
  esac
  if [ -n "$raw" ] && ! [[ "$raw" =~ ^[A-Za-z0-9._-]+$ ]]; then
    raw=""
  fi
  printf '%s' "$raw"
}

ANTHROPIC_MODEL="$(sanitize_model "${ANTHROPIC_MODEL:-}")"
OPENAI_MODEL="$(sanitize_model "${OPENAI_MODEL:-}")"
if [ "$ANTHROPIC_MODEL" = "org_default" ]; then
  ANTHROPIC_MODEL=""
fi
if [ -z "$ANTHROPIC_MODEL" ]; then
  ANTHROPIC_MODEL="claude-opus-4-5-20251101"
fi
if [ -z "$OPENAI_MODEL" ]; then
  OPENAI_MODEL="gpt-5.2"
fi

PR_TITLE=$(< pr_title.txt)
PR_BODY=$(python3 -c 'from pathlib import Path; import sys; s=Path("pr_body.txt").read_text(encoding="utf-8", errors="replace"); sys.stdout.write(s[:3000])')
PR_AUTHOR=$(< pr_author.txt)
PR_ADDITIONS=$(< pr_additions.txt)
PR_DELETIONS=$(< pr_deletions.txt)
PR_FILES_COUNT=$(< pr_files_count.txt)
PR_CHECKS_SUMMARY=$(python3 -c 'from pathlib import Path; import sys; p=Path("pr_checks_summary.txt"); s=p.read_text(encoding="utf-8", errors="replace") if p.exists() else ""; sys.stdout.write(s[:8000])')
PR_CHECKS_FAILED=$(python3 -c 'from pathlib import Path; import sys; p=Path("pr_checks_failed_count.txt"); s=p.read_text(encoding="utf-8", errors="replace").strip() if p.exists() else "0"; sys.stdout.write(s if s else "0")')

DIFF_SCOPE="full"
if [ -f pr_diff_scope.txt ]; then
  DIFF_SCOPE=$(< pr_diff_scope.txt)
fi

DIFF_RANGE=""
if [ -f pr_diff_range.txt ]; then
  DIFF_RANGE=$(< pr_diff_range.txt)
fi

  PR_DIFF_RAW=""
  if [ -f pr_diff.txt ]; then
    PR_DIFF_RAW=$(< pr_diff.txt)
  fi
  PR_DIFF_SIZE=${#PR_DIFF_RAW}
  if [ "$PR_DIFF_SIZE" -gt 100000 ]; then
    PR_DIFF="$(python3 -c 'import sys; s=sys.stdin.buffer.read().decode("utf-8", "replace"); sys.stdout.write(s[:50000] + "\n\n... (snip) ...\n\n" + s[-50000:])' <<< "$PR_DIFF_RAW")"
  else
    PR_DIFF="$PR_DIFF_RAW"
  fi
  
  PREV_REVIEW=""
  if [ -f prev_merglbot_review.txt ]; then
    PREV_REVIEW=$(python3 -c 'from pathlib import Path; import sys; p=Path("prev_merglbot_review.txt"); sys.stdout.write(p.read_text(encoding="utf-8", errors="replace")[:20000])' 2>/dev/null || true)
  fi
  
  NEW_COMMITS=""
  if [ -f new_commits.txt ]; then
    NEW_COMMITS=$(python3 -c 'from pathlib import Path; import sys; p=Path("new_commits.txt"); sys.stdout.write(p.read_text(encoding="utf-8", errors="replace")[:5000])' 2>/dev/null || true)
  fi

CHANGED_FILES=$(head -100 changed_files.txt 2>/dev/null | tr '\n' ', ')

BUGBOT_FINDINGS=""
if [ -f bugbot_findings.txt ]; then
  BUGBOT_FINDINGS=$(< bugbot_findings.txt)
fi

BUGBOT_COUNT="0"
if [ -f bugbot_count.txt ]; then
  BUGBOT_COUNT=$(< bugbot_count.txt)
fi

BUGBOT_SOURCES="none"
if [ -f bugbot_sources.txt ]; then
  BUGBOT_SOURCES=$(< bugbot_sources.txt)
fi

echo "Context loaded:"
echo "  PR Body: ${#PR_BODY} chars"
echo "  PR Diff: ${#PR_DIFF} chars"
echo "  Bugbot Sources: $BUGBOT_SOURCES ($BUGBOT_COUNT)"

if [ "${REVIEW_MODE}" == "light" ]; then
  REVIEW_DEPTH="LIGHT"
  OUTPUT_INSTRUCTIONS="Output a CONCISE review (max 500 words). Focus only on critical and high priority issues."
  MAX_TOKENS_ANTHROPIC=8000
  MAX_TOKENS_OPENAI=32000
else
  REVIEW_DEPTH="FULL"
  OUTPUT_INSTRUCTIONS="Output a COMPREHENSIVE review with detailed analysis, code examples, MERGLBOT rule references, and actionable checkboxes."
  MAX_TOKENS_ANTHROPIC=16000
  MAX_TOKENS_OPENAI=65000
fi

echo "Review depth: $REVIEW_DEPTH"

# Build prompt using printf to file (single redirect)
{
printf '%s\n' "# Merglbot Multi-Model Code Review v3.3"
printf '%s\n' ""
printf '%s\n' "You are a senior code reviewer for Merglbot - a platform for AI-powered code intelligence."
printf '%s\n' ""
printf '%s\n' "## MERGLBOT AI AGENT APPENDIX v2.15"
printf '%s\n' ""
printf '%s\n' "This is your authoritative reference for all Merglbot standards."
printf '%s\n' ""
printf '%s\n' "### Critical Rules (MUST FOLLOW)"
printf '%s\n' ""
printf '%s\n' "1. PR Hygiene: Push na STEJNY PR/branch, nevytvarej duplikaty"
printf '%s\n' "2. Production: Vse pres PR + schvaleni, nikdy push na main"
printf '%s\n' "3. Security: Zadne secrets v kodu; jen GitHub secrets; zadne SA JSON - vzdy WIF/OIDC"
printf '%s\n' "4. Auth: Auth V2 Multi-Segment (platform_admin/client/demo) - viz AUTHENTICATION_AUTHORIZATION.md"
printf '%s\n' "5. Workflow: Plan - Act - Verify"
printf '%s\n' "6. PR Size: Dle MERGLBOT_PR_SIZE_AND_REVIEW_HYGIENE.md (vyjimka jen u cistych docs) - MERGLBOT-PR-001"
printf '%s\n' "7. Commits: Conventional (feat:, fix:, docs:, chore:, ci:)"
printf '%s\n' "8. Branch: feat/, fix/, docs/, ci/ - vzdy squash merge do main"
printf '%s\n' "9. SSOT: Dokumentace v merglbot-public/docs/"
printf '%s\n' "10. Destructive: Double-confirm pred destruktivni akci"
printf '%s\n' "11. CI Idempotency: CI kroky musi byt idempotentni"
printf '%s\n' "12. Security Gating: Trivy/CodeQL gaty se NEzmekuji"
printf '%s\n' ""
printf '%s\n' "### MERGLBOT Rule Reference"
printf '%s\n' ""
printf '%s\n' "MERGLBOT-SEC-001: No hardcoded secrets - use env vars or Secret Manager"
printf '%s\n' "MERGLBOT-SEC-002: Container hardening - Trivy HIGH/CRITICAL must pass"
printf '%s\n' "MERGLBOT-SEC-003: OIDC/WIF over SA JSON keys"
printf '%s\n' "MERGLBOT-SEC-004: No secrets in logs"
printf '%s\n' "MERGLBOT-CI-001: GHA standards - pinned actions, minimal permissions"
printf '%s\n' "MERGLBOT-CI-002: Idempotent CI steps"
printf '%s\n' "MERGLBOT-PR-001: PR size limits (MERGLBOT_PR_SIZE_AND_REVIEW_HYGIENE.md)"
printf '%s\n' "MERGLBOT-PR-002: Conventional commits"
printf '%s\n' "MERGLBOT-ARCH-001: Auth via AUTHENTICATION_AUTHORIZATION.md"
printf '%s\n' ""
printf '%s\n' "### SSOT Documentation Links"
printf '%s\n' ""
printf '%s\n' "- https://github.com/merglbot-public/docs/blob/main/RULEBOOK_V2.md - Platform rules"
printf '%s\n' "- https://github.com/merglbot-public/docs/blob/main/PR_POLICY.md - PR requirements"
printf '%s\n' "- https://github.com/merglbot-public/docs/blob/main/SECURITY.md - Security"
printf '%s\n' "- https://github.com/merglbot-public/docs/blob/main/MERGLBOT_SECRETS_NAMING_AND_LOGGING.md - Secrets"
printf '%s\n' "- https://github.com/merglbot-public/docs/blob/main/MERGLBOT_GITHUB_ACTIONS_GLOBAL_RULES.md - GHA"
printf '%s\n' "- https://github.com/merglbot-public/docs/blob/main/AUTHENTICATION_AUTHORIZATION.md - Auth"
printf '%s\n' ""
printf '%s\n' "---"
printf '%s\n' ""
printf '%s\n' "## REVIEW SCOPE"
printf '%s\n' ""
printf '%s\n' "Scope: $DIFF_SCOPE"
printf '%s\n' "Commit Range: $DIFF_RANGE"
if [ "$DIFF_SCOPE" == "delta" ]; then
  printf '%s\n' ""
  printf '%s\n' "IMPORTANT: This is a DELTA review. Focus ONLY on changes introduced in this diff."
  printf '%s\n' "Do NOT repeat findings about unchanged code unless the new changes re-introduce the issue."
  printf '%s\n' "Use Previous Review section to mark what is resolved vs still relevant."
fi
printf '%s\n' ""
printf '%s\n' "---"
printf '%s\n' ""
printf '%s\n' "## YOUR MISSION"
printf '%s\n' ""
printf '%s\n' "Perform a thorough, professional code review that:"
printf '%s\n' "1. Focuses on what matters - security, bugs, architecture"
printf '%s\n' "2. Respects PRs stated goal - dont suggest unrelated changes"
printf '%s\n' "3. Analyzes ALL bugbot findings - validate, filter false positives"
printf '%s\n' "4. Follows Merglbot standards - cite MERGLBOT rules"
printf '%s\n' "5. Provides actionable feedback with code examples"
printf '%s\n' ""
printf '%s\n' "## OUTPUT REQUIREMENTS"
printf '%s\n' ""
printf '%s\n' "Review Mode: $REVIEW_DEPTH"
printf '%s\n' "$OUTPUT_INSTRUCTIONS"
printf '%s\n' ""
printf '%s\n' "### Output Structure"
printf '%s\n' ""
printf '%s\n' "# Code Review Sumar"
printf '%s\n' "[3-5 sentences about PR, quality, concerns]"
printf '%s\n' ""
printf '%s\n' "## Findings"
printf '%s\n' ""
printf '%s\n' "### Critical (Must Fix)"
printf '%s\n' "[List with code examples and MERGLBOT rule citations]"
printf '%s\n' "- [ ] Finding: Description (MERGLBOT-XXX)"
printf '%s\n' ""
printf '%s\n' "### High Priority"
printf '%s\n' "[List]"
printf '%s\n' ""
printf '%s\n' "### Medium Priority"
printf '%s\n' "[List]"
printf '%s\n' ""
printf '%s\n' "### Low Priority"
printf '%s\n' "[List]"
printf '%s\n' ""
printf '%s\n' "## Whats Good"
printf '%s\n' "[2-4 positive aspects]"
printf '%s\n' ""
printf '%s\n' "## Bugbot Findings Analysis"
printf '%s\n' "[For EACH bugbot finding state: Agree/Disagree/Partially with reason]"
printf '%s\n' ""
printf '%s\n' "## SSOT Sync (Docs)"
printf '%s\n' "[List docs in merglbot-public/docs that must be updated due to this PR; if none: 'None']"
printf '%s\n' "- [ ] Doc: path - what changed + what to update"
printf '%s\n' ""

if [ "${INCLUDE_RETRO:-false}" == "true" ]; then
  printf '%s\n' "## Retro (Extractable Learnings)"
  printf '%s\n' "[1-3 items max. Each item: Problem, Trigger Conditions, Root Cause, Fix/Workaround, Verification. If too tactical for SSOT, propose a LOCAL personal skill (not committed) with a SKILL.md skeleton.]"
  printf '%s\n' ""
fi

printf '%s\n' "## Zaver"
printf '%s\n' "Verdict: [APPROVE or CHANGES NEEDED]"
printf '%s\n' "[1-2 sentences]"
printf '%s\n' ""
printf '%s\n' "### Rules"
printf '%s\n' "- Czech for explanations, English for code"
printf '%s\n' "- Include line numbers"
printf '%s\n' "- Evidence-first: every finding must cite file:line AND a diff excerpt; otherwise mark it as 'Needs verification'"
printf '%s\n' "- Cite MERGLBOT rules"
printf '%s\n' "- Code examples for fixes"
printf '%s\n' "- Use checkboxes for actions"
printf '%s\n' ""
printf '%s\n' "---"
printf '%s\n' ""
printf '%s\n' "## PR INFORMATION"
printf '%s\n' ""
printf '%s\n' "Title: $PR_TITLE"
printf '%s\n' "PR Number: #$PR_NUMBER"
printf '%s\n' "Author: @$PR_AUTHOR"
printf '%s\n' "Changes: +$PR_ADDITIONS / -$PR_DELETIONS in $PR_FILES_COUNT files"
printf '%s\n' "Changed Files: $CHANGED_FILES"
printf '%s\n' ""
printf '%s\n' "### CI / Checks Summary"
printf '%s\n' ""
printf '%s\n' "(Failed checks (best-effort): $PR_CHECKS_FAILED)"
printf '%s\n' ""
printf '%s\n' "$PR_CHECKS_SUMMARY"
printf '%s\n' ""
printf '%s\n' "### PR Description"
printf '%s\n' ""
printf '%s\n' "$PR_BODY"
printf '%s\n' ""
printf '%s\n' "---"
printf '%s\n' ""
printf '%s\n' "## BUGBOT FINDINGS ($BUGBOT_COUNT sources: $BUGBOT_SOURCES)"
printf '%s\n' ""
printf '%s\n' "For EACH finding below, state: Agree/Disagree/Partially"
if [ -n "${REVIEW_CUTOFF:-}" ]; then
  printf '%s\n' "(Note: bugbot findings are filtered to comments after: ${REVIEW_CUTOFF:-unknown})"
fi
printf '%s\n' ""
printf '%s\n' "$BUGBOT_FINDINGS"
printf '%s\n' ""
printf '%s\n' "---"
printf '%s\n' ""

if [ "$DIFF_SCOPE" == "delta" ] && [ -n "$PREV_REVIEW" ]; then
  printf '%s\n' "## PREVIOUS MERGLBOT REVIEW (for delta context)"
  printf '%s\n' ""
  printf '%s\n' "$PREV_REVIEW"
  printf '%s\n' ""
  printf '%s\n' "---"
  printf '%s\n' ""
  printf '%s\n' "## NEW COMMITS SINCE PREVIOUS REVIEW"
  printf '%s\n' ""
  printf '%s\n' "$NEW_COMMITS"
  printf '%s\n' ""
  printf '%s\n' "---"
  printf '%s\n' ""
fi
printf '%s\n' "## PR DIFF"
printf '%s\n' ""
echo '```diff'
printf '%s\n' "$PR_DIFF"
echo '```'
printf '%s\n' ""
printf '%s\n' "---"
printf '%s\n' ""
printf '%s\n' "Now provide your review following the structure above."

} > /tmp/full_prompt.txt
FULL_PROMPT=$(< /tmp/full_prompt.txt)
PROMPT_SIZE=${#FULL_PROMPT}
echo "Prompt size: $PROMPT_SIZE chars"

# ANTHROPIC CALL
echo "Calling Anthropic (requested: $ANTHROPIC_MODEL)..."

ANTHROPIC_MODEL_USED=""
ANTHROPIC_MODELS_TRIED="|"
for MODEL_TO_TRY in "$ANTHROPIC_MODEL" "claude-opus-4-5-20251101" "claude-opus-4-5-20250929" "claude-sonnet-4-5-20250929" "claude-opus-4-1-20250805" "claude-3-5-haiku-20241022"; do
  if [ -z "$MODEL_TO_TRY" ] || [ "$MODEL_TO_TRY" = "null" ]; then
    continue
  fi
  case "$ANTHROPIC_MODELS_TRIED" in
    *"|$MODEL_TO_TRY|"*) continue ;;
  esac
  ANTHROPIC_MODELS_TRIED="${ANTHROPIC_MODELS_TRIED}${MODEL_TO_TRY}|"
  echo "  → Trying Anthropic model: $MODEL_TO_TRY"

  jq -n \
    --arg model "$MODEL_TO_TRY" \
    --rawfile prompt /tmp/full_prompt.txt \
    --argjson max_tokens "$MAX_TOKENS_ANTHROPIC" \
    '{
      model: $model,
      max_tokens: $max_tokens,
      temperature: 0.2,
      messages: [{role: "user", content: $prompt}]
    }' > /tmp/anthropic_payload.json
  
  set +e
  ANTHROPIC_RESP=$(curl -s --retry 2 --retry-all-errors --max-time 180 https://api.anthropic.com/v1/messages \
    -H "content-type: application/json" \
    -H "x-api-key: $ANTHROPIC_API_KEY" \
    -H "anthropic-version: $ANTHROPIC_API_VERSION" \
    -d @/tmp/anthropic_payload.json)
  CURL_EXIT=$?
  set -e
  
  if [ "$CURL_EXIT" -ne 0 ] || ! echo "$ANTHROPIC_RESP" | jq -e . > /dev/null 2>&1; then
    echo "  ERROR: Anthropic request failed or returned non-JSON (curl exit=$CURL_EXIT)"
    continue
  fi
  
  if echo "$ANTHROPIC_RESP" | jq -e ".error" > /dev/null 2>&1; then
    echo "  ERROR: $(echo "$ANTHROPIC_RESP" | jq -r '.error.message')"
    continue
  fi
  
  ANTHROPIC_CONTENT=$(echo "$ANTHROPIC_RESP" | jq -r '.content[0].text // empty')
  if [ -z "$ANTHROPIC_CONTENT" ] || [ "$ANTHROPIC_CONTENT" = "null" ]; then
    echo "  ERROR: Anthropic response contained no content"
    continue
  fi
  
  ANTHROPIC_MODEL_USED="$MODEL_TO_TRY"
  echo "Success (model: $ANTHROPIC_MODEL_USED)"
  echo "Words: $(echo "$ANTHROPIC_CONTENT" | wc -w)"
  echo "$ANTHROPIC_CONTENT" > anthropic_review.txt
  break
done

if [ ! -s anthropic_review.txt ]; then
  echo "API_ERROR" > anthropic_review.txt
fi

if [ -z "$ANTHROPIC_MODEL_USED" ]; then
  ANTHROPIC_MODEL_USED="$ANTHROPIC_MODEL"
fi
echo "ANTHROPIC_MODEL_USED=$ANTHROPIC_MODEL_USED" >> "$GITHUB_ENV"

extract_output_text_responses() {
  local json="$1"
  local out=""
  out="$(echo "$json" | jq -r '.output_text // empty' 2>/dev/null || true)"
  if [ -z "$out" ] || [ "$out" = "null" ]; then
    out="$(echo "$json" | jq -r '[.output[]? | select(.type=="message") | .content[]? | select(.type=="output_text") | .text] | join("\n")' 2>/dev/null || true)"
  fi
  if [ -z "$out" ] || [ "$out" = "null" ]; then
    out="$(echo "$json" | jq -r '.output[0].content[0].text // empty' 2>/dev/null || true)"
  fi
  printf '%s' "$out"
}

call_openai_responses() {
  local model="$1"
  local max_tokens="$2"
  local prompt_file="$3"

  local payload_a="/tmp/openai_responses_payload_a.json"
  local payload_b="/tmp/openai_responses_payload_b.json"
  local payload_c="/tmp/openai_responses_payload_c.json"

  jq -n \
    --arg model "$model" \
    --rawfile prompt "$prompt_file" \
    --argjson max_output_tokens "$max_tokens" \
    '{
      model: $model,
      input: $prompt,
      max_output_tokens: $max_output_tokens,
      reasoning: { effort: "high" }
    }' > "$payload_a"

  jq -n \
    --arg model "$model" \
    --rawfile prompt "$prompt_file" \
    --argjson max_output_tokens "$max_tokens" \
    '{
      model: $model,
      input: [{ role: "user", content: $prompt }],
      max_output_tokens: $max_output_tokens,
      reasoning: { effort: "high" }
    }' > "$payload_b"

  jq -n \
    --arg model "$model" \
    --rawfile prompt "$prompt_file" \
    --argjson max_output_tokens "$max_tokens" \
    '{
      model: $model,
      input: [{ role: "user", content: [{ type: "input_text", text: $prompt }] }],
      max_output_tokens: $max_output_tokens,
      reasoning: { effort: "high" }
    }' > "$payload_c"

  for payload in "$payload_a" "$payload_b" "$payload_c"; do
    local variant="A"
    if [ "$payload" = "$payload_b" ]; then
      variant="B"
    elif [ "$payload" = "$payload_c" ]; then
      variant="C"
    fi
    echo "  Trying Responses API payload variant ${variant}..." >&2

    set +e
    local resp
    resp="$(curl -s --retry 2 --retry-all-errors --max-time 180 https://api.openai.com/v1/responses \
      -H "Content-Type: application/json" \
      -H "Authorization: Bearer $OPENAI_API_KEY" \
      -d @"$payload")"
    local exit_code=$?
    set -e

    if [ "$exit_code" -ne 0 ] || ! echo "$resp" | jq -e . > /dev/null 2>&1; then
      echo "  ERROR: Responses API returned non-JSON (exit=$exit_code)" >&2
      continue
    fi
    if echo "$resp" | jq -e ".error" > /dev/null 2>&1; then
      echo "  ERROR: $(echo "$resp" | jq -r '.error.message')" >&2
      continue
    fi

    local out
    out="$(extract_output_text_responses "$resp")"
    if [ -z "$out" ] || [ "$out" = "null" ]; then
      echo "  ERROR: Responses API contained no output_text" >&2
      continue
    fi

    # Log token usage if present (do not fail if missing)
    local total_tokens input_tokens output_tokens reasoning_tokens
    total_tokens="$(echo "$resp" | jq -r '.usage.total_tokens // 0' 2>/dev/null || echo 0)"
    input_tokens="$(echo "$resp" | jq -r '.usage.input_tokens // .usage.prompt_tokens // 0' 2>/dev/null || echo 0)"
    output_tokens="$(echo "$resp" | jq -r '.usage.output_tokens // .usage.completion_tokens // 0' 2>/dev/null || echo 0)"
    reasoning_tokens="$(echo "$resp" | jq -r '.usage.output_tokens_details.reasoning_tokens // .usage.completion_tokens_details.reasoning_tokens // 0' 2>/dev/null || echo 0)"
    if [ "$total_tokens" != "0" ] || [ "$input_tokens" != "0" ] || [ "$output_tokens" != "0" ]; then
      echo "  Token usage:" >&2
      echo "    Input: $input_tokens" >&2
      echo "    Output: $output_tokens (reasoning: $reasoning_tokens)" >&2
      echo "    Total: $total_tokens" >&2
    fi

    # Persist numeric usage for later metrics artifact (no secrets).
    OPENAI_USAGE_API="responses"
    OPENAI_USAGE_INPUT_TOKENS="$input_tokens"
    OPENAI_USAGE_OUTPUT_TOKENS="$output_tokens"
    OPENAI_USAGE_REASONING_TOKENS="$reasoning_tokens"
    OPENAI_USAGE_TOTAL_TOKENS="$total_tokens"

    printf '%s' "$out"
    return 0
  done

  return 1
}

# Default usage fields (filled on success; safe to write even if zeros).
OPENAI_USAGE_API="unknown"
OPENAI_USAGE_INPUT_TOKENS=0
OPENAI_USAGE_OUTPUT_TOKENS=0
OPENAI_USAGE_REASONING_TOKENS=0
OPENAI_USAGE_TOTAL_TOKENS=0

# OPENAI CALL
echo "Calling OpenAI (requested: $OPENAI_MODEL)..."

OPENAI_MODEL_USED=""
OPENAI_MODELS_TRIED="|"
for MODEL_TO_TRY in "$OPENAI_MODEL" "gpt-5.2" "gpt-5.1" "gpt-5" "gpt-4-turbo"; do
  if [ -z "$MODEL_TO_TRY" ] || [ "$MODEL_TO_TRY" = "null" ]; then
    continue
  fi
  case "$OPENAI_MODELS_TRIED" in
    *"|$MODEL_TO_TRY|"*) continue ;;
  esac
  OPENAI_MODELS_TRIED="${OPENAI_MODELS_TRIED}${MODEL_TO_TRY}|"
  echo "  → Trying OpenAI model: $MODEL_TO_TRY"

  # Prefer Responses API for GPT-5.*. Fall back to Chat Completions for legacy models.
  USE_CHAT="false"
  if [ "$MODEL_TO_TRY" = "gpt-4-turbo" ] || echo "$MODEL_TO_TRY" | grep -Eqi '^o[13]'; then
    USE_CHAT="true"
  fi

  if [ "$USE_CHAT" == "false" ]; then
    echo "  → Using Responses API"
    OPENAI_RESPONSES_OUT="$(mktemp)"
    if call_openai_responses "$MODEL_TO_TRY" "$MAX_TOKENS_OPENAI" "/tmp/full_prompt.txt" > "$OPENAI_RESPONSES_OUT"; then
      OPENAI_MODEL_USED="$MODEL_TO_TRY"
      echo "Success (model: $OPENAI_MODEL_USED)"
      echo "Words: $(wc -w < "$OPENAI_RESPONSES_OUT")"
      mv -f "$OPENAI_RESPONSES_OUT" openai_review.txt
      break
    fi
    rm -f "$OPENAI_RESPONSES_OUT"
    echo "  WARN: Responses API failed; falling back to Chat Completions"

    jq -n \
      --arg model "$MODEL_TO_TRY" \
      --rawfile prompt /tmp/full_prompt.txt \
      --argjson max_tokens "$MAX_TOKENS_OPENAI" \
      '{
        model: $model,
        messages: [{role: "user", content: $prompt}],
        max_completion_tokens: $max_tokens,
        reasoning_effort: "high"
      }' > /tmp/openai_payload.json

    set +e
    OPENAI_RESP=$(curl -s --retry 2 --retry-all-errors --max-time 180 https://api.openai.com/v1/chat/completions \
      -H "Content-Type: application/json" \
      -H "Authorization: Bearer $OPENAI_API_KEY" \
      -d @/tmp/openai_payload.json)
    CURL_EXIT=$?
    set -e

    if [ "$CURL_EXIT" -ne 0 ] || ! echo "$OPENAI_RESP" | jq -e . > /dev/null 2>&1; then
      echo "  ERROR: OpenAI request failed or returned non-JSON (curl exit=$CURL_EXIT)"
      continue
    fi

    if echo "$OPENAI_RESP" | jq -e ".error" > /dev/null 2>&1; then
      echo "  ERROR: $(echo "$OPENAI_RESP" | jq -r '.error.message')"
      continue
    fi

    CONTENT=$(echo "$OPENAI_RESP" | jq -r '.choices[0].message.content // empty')
    REFUSAL=$(echo "$OPENAI_RESP" | jq -r '.choices[0].message.refusal // empty')
    if [ -n "$REFUSAL" ] && [ "$REFUSAL" != "null" ]; then
      echo "  ERROR: OpenAI response refusal"
      continue
    fi
    if [ -z "$CONTENT" ] || [ "$CONTENT" = "null" ]; then
      echo "  ERROR: OpenAI response contained no content"
      continue
    fi

    OPENAI_MODEL_USED="$MODEL_TO_TRY"
    echo "Success (model: $OPENAI_MODEL_USED)"
    echo "Words: $(echo "$CONTENT" | wc -w)"
    echo "$CONTENT" > openai_review.txt

    # Persist token usage for fallback Chat Completions.
    OPENAI_USAGE_API="chat_completions"
    OPENAI_USAGE_TOTAL_TOKENS="$(echo "$OPENAI_RESP" | jq -r '.usage.total_tokens // 0' 2>/dev/null || echo 0)"
    OPENAI_USAGE_INPUT_TOKENS="$(echo "$OPENAI_RESP" | jq -r '.usage.prompt_tokens // 0' 2>/dev/null || echo 0)"
    OPENAI_USAGE_OUTPUT_TOKENS="$(echo "$OPENAI_RESP" | jq -r '.usage.completion_tokens // 0' 2>/dev/null || echo 0)"
    OPENAI_USAGE_REASONING_TOKENS="$(echo "$OPENAI_RESP" | jq -r '.usage.completion_tokens_details.reasoning_tokens // 0' 2>/dev/null || echo 0)"
    break
  fi

  echo "  → Using Chat Completions API"
  if [ "$MODEL_TO_TRY" = "gpt-4-turbo" ]; then
    MAX_TOKENS_TURBO="$MAX_TOKENS_OPENAI"
    if [ "$MAX_TOKENS_TURBO" -gt 4096 ]; then
      MAX_TOKENS_TURBO=4096
    fi
    jq -n \
      --arg model "$MODEL_TO_TRY" \
      --rawfile prompt /tmp/full_prompt.txt \
      --argjson max_tokens "$MAX_TOKENS_TURBO" \
      '{
        model: $model,
        messages: [{role: "user", content: $prompt}],
        max_tokens: $max_tokens
      }' > /tmp/openai_payload.json
  else
    jq -n \
      --arg model "$MODEL_TO_TRY" \
      --rawfile prompt /tmp/full_prompt.txt \
      --argjson max_tokens "$MAX_TOKENS_OPENAI" \
      '{
        model: $model,
        messages: [{role: "user", content: $prompt}],
        max_completion_tokens: $max_tokens
      }' > /tmp/openai_payload.json
  fi

  set +e
  OPENAI_RESP=$(curl -s --retry 2 --retry-all-errors --max-time 180 https://api.openai.com/v1/chat/completions \
    -H "Content-Type: application/json" \
    -H "Authorization: Bearer $OPENAI_API_KEY" \
    -d @/tmp/openai_payload.json)
  CURL_EXIT=$?
  set -e

  if [ "$CURL_EXIT" -ne 0 ] || ! echo "$OPENAI_RESP" | jq -e . > /dev/null 2>&1; then
    echo "  ERROR: OpenAI request failed or returned non-JSON (curl exit=$CURL_EXIT)"
    continue
  fi

  if echo "$OPENAI_RESP" | jq -e ".error" > /dev/null 2>&1; then
    echo "  ERROR: $(echo "$OPENAI_RESP" | jq -r '.error.message')"
    continue
  fi

  CONTENT=$(echo "$OPENAI_RESP" | jq -r '.choices[0].message.content // empty')
  REFUSAL=$(echo "$OPENAI_RESP" | jq -r '.choices[0].message.refusal // empty')
  if [ -n "$REFUSAL" ] && [ "$REFUSAL" != "null" ]; then
    echo "  ERROR: OpenAI response refusal"
    continue
  fi
  if [ -z "$CONTENT" ] || [ "$CONTENT" = "null" ]; then
    echo "  ERROR: OpenAI response contained no content"
    continue
  fi

  OPENAI_MODEL_USED="$MODEL_TO_TRY"
  echo "Success (model: $OPENAI_MODEL_USED)"

  TOTAL_TOKENS=$(echo "$OPENAI_RESP" | jq -r '.usage.total_tokens // 0')
  PROMPT_TOKENS=$(echo "$OPENAI_RESP" | jq -r '.usage.prompt_tokens // 0')
  COMPLETION_TOKENS=$(echo "$OPENAI_RESP" | jq -r '.usage.completion_tokens // 0')
  REASONING_TOKENS=$(echo "$OPENAI_RESP" | jq -r '.usage.completion_tokens_details.reasoning_tokens // 0')
  OUTPUT_TOKENS=$((COMPLETION_TOKENS - REASONING_TOKENS))
  echo "  Token usage:"
  echo "    Prompt: $PROMPT_TOKENS"
  echo "    Completion: $COMPLETION_TOKENS (reasoning: $REASONING_TOKENS, output: $OUTPUT_TOKENS)"
  echo "    Total: $TOTAL_TOKENS"

  OPENAI_USAGE_API="chat_completions"
  OPENAI_USAGE_TOTAL_TOKENS="$TOTAL_TOKENS"
  OPENAI_USAGE_INPUT_TOKENS="$PROMPT_TOKENS"
  OPENAI_USAGE_OUTPUT_TOKENS="$COMPLETION_TOKENS"
  OPENAI_USAGE_REASONING_TOKENS="$REASONING_TOKENS"

  echo "Words: $(echo "$CONTENT" | wc -w)"
  echo "$CONTENT" > openai_review.txt
  break
done

if [ ! -s openai_review.txt ]; then
  echo "API_ERROR" > openai_review.txt
fi

if [ -z "$OPENAI_MODEL_USED" ]; then
  OPENAI_MODEL_USED="$OPENAI_MODEL"
fi
echo "OPENAI_MODEL_USED=$OPENAI_MODEL_USED" >> "$GITHUB_ENV"

cat > openai_usage.json << EOF
{
  "api": "${OPENAI_USAGE_API}",
  "input_tokens": ${OPENAI_USAGE_INPUT_TOKENS},
  "output_tokens": ${OPENAI_USAGE_OUTPUT_TOKENS},
  "reasoning_tokens": ${OPENAI_USAGE_REASONING_TOKENS},
  "total_tokens": ${OPENAI_USAGE_TOTAL_TOKENS}
}
EOF

echo "========================================="
echo "STEP 1 COMPLETE"
echo "========================================="
      
