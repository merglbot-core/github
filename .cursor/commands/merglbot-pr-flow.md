# Merglbot PR Flow (Branch + PR + Review)

<!-- Source: merglbot-public/docs/templates/cursor/.cursor/commands/merglbot-pr-flow.md -->

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
   - Trigger review tooling installed in the repo (e.g. comment-based bots). If unsure, follow SSOT PR review docs.

7. **Merge**
   - Squash merge
   - Prefer auto-merge after checks + approval
   - Keep branch up-to-date before merging (if needed)

## SSOT references

- PR review quick start: `merglbot-public/docs/MERGLBOT_PR_REVIEW_QUICK_START.md`
- PR size hygiene: `merglbot-public/docs/MERGLBOT_PR_SIZE_AND_REVIEW_HYGIENE.md`
- Agent rules (SSOT): `merglbot-public/docs/RULEBOOK_V2.md` (or https://github.com/merglbot-public/docs/blob/main/RULEBOOK_V2.md)
- Optional appendix (if present in workspace): `merglbot-core/ai_prompts/agent-appendix/`
