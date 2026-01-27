# Merglbot PR Flow (Branch + PR + Review)

Use this when you are about to open or update a PR in any `merglbot-*` repo.

## Checklist

1. **Blueprint-first (before coding)**
   - Spec/blueprint exists (README/SPEC.md or PR description)
   - Acceptance criteria are explicit
   - Edge cases are listed
   - Dependencies are identified (e.g., verify declarations exist in `package.json`, `requirements.txt`, `pyproject.toml`, `go.mod`)

2. **Branching + scope**
   - One change = one branch
   - Branch prefix: `feat/`, `fix/`, `docs/`, `ci/`
   - Never push directly to `main`

3. **Commits**
   - Conventional commits (`feat:`, `fix:`, `docs:`, `chore:`, `ci:`)
   - No secrets in commit messages or diffs (names only)

4. **Pre-flight verification (scoped)**
   - Run only what’s relevant to touched areas
   - Infra: `terraform fmt -check`, `terraform validate`, `tflint` (if available)
   - JS/TS: lint + tests/build as appropriate

5. **SSOT sync (“Documentation = Reality”)**
   - After any code/infra change: run `/merglbot-doc-sync`
   - If you learned something non-obvious: run `/merglbot-retro`

6. **PR hygiene**
   - Keep PR small (MERGLBOT-PR-001)
   - Include in PR description:
     - Summary (why)
     - Risk/impact
     - Test plan (checklist)
   - Trigger review tool installed in the repo (default: comment `@merglbot review`)
   - If using multiple comment-based bots: `/gemini review` + `@cursor review` first (wait), then `@merglbot review` last (avoids cancellations)

7. **Merge**
   - Squash merge
   - Prefer auto-merge after checks + approval
   - Keep branch up-to-date before merging (if needed)

## SSOT references

- Agent rules: `merglbot-core/ai_prompts/agent-appendix/MERGLBOT_AI_AGENT_APPENDIX_v2_15.md`
- PR review methodology: `merglbot-core/ai_prompts/pr-review/MERGLBOT_PR_REVIEW_AUTONOMOUS_V5.md`
- PR size hygiene: `merglbot-public/docs/MERGLBOT_PR_SIZE_AND_REVIEW_HYGIENE.md`
