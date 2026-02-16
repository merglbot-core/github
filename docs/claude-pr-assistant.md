# ⚠️ DEPRECATED: Claude PR Assistant v2

> **This document is deprecated.** PR Assistant v3 is now the standard.
> See [MERGLBOT_PR_ASSISTANT_V3.md](https://github.com/merglbot-public/docs/blob/main/MERGLBOT_PR_ASSISTANT_V3.md)

---

## Quick Start (v3)

On any PR in any Merglbot repository, comment:

```
@merglbot review
```

This triggers a multi-model review using **Claude Opus 4.6 + GPT-5.2 (HIGH reasoning)** with **final synthesis on Claude**.

For lighter review: `@merglbot review --light`

---

## Current Workflow Location

- **Source**: `.github/workflows/merglbot-pr-assistant-v3-on-demand.yml`
- **Tag**: `merglbot-core/github@v3.4.0`
- **Coverage**: 100% (22 repos across 10 organizations; 3 orgs empty/pre-configured)

---

## Deprecated Content Below

> The following content is kept for historical reference only.

---

# Claude PR Assistant v2 (Reusable Workflows) - DEPRECATED

## Overview

This repository provides centralized, reusable GitHub Actions workflows for AI-assisted PR reviews and text length validation across the Merglbot enterprise.

**Workflows (DEPRECATED):**
- `claude-pr-assistant-preview.yml` – REMOVED
- `claude-pr-assistant.yml` – REMOVED  
- `length-check.yml` – Still available

---

## Usage (DEPRECATED)

> **Do not use this pattern.** Use `@merglbot review` comment instead.

~~Add this file to your repository: `.github/workflows/ai-pr.yml`~~

```yaml
# DEPRECATED - DO NOT USE
name: AI PR Assistant (v2 preview)

on:
  pull_request:
    types: [opened, edited, synchronize, reopened]

permissions:
  contents: read
  pull-requests: write
  issues: write

jobs:
  ai-review:
    uses: merglbot-core/github/.github/workflows/claude-pr-assistant-preview.yml@mcp/ai-pr-v2-20251010-1202
    secrets:
      ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
    with:
      trigger_labels: "ai-review"
      skip_labels: "no-ai"
      model: "claude-3-5-sonnet-20241022"
```

**Notes:**
- Replace `@mcp/ai-pr-v2-20251010-1202` with a stable tag (e.g., `@v2.0.0-rc.1` or `@v2`) after testing.
- Workflow only runs when PR has label `ai-review`; skips if `no-ai` label present.
- Update-in-place comments (no spam).

---

## Secrets

**Required secret (org or repo level):**
- `ANTHROPIC_API_KEY` – Do NOT use prefix `GITHUB_*` (reserved).

**Setup (org-level recommended):**
1. Go to Organization settings → Security → Secrets and variables → Actions
2. New organization secret: `ANTHROPIC_API_KEY`
3. Value: [your Anthropic API key]
4. Access: All repositories (or selected)

---

## Required Workflow: PR Text Length Check

**Purpose:** Enforce MERGLBOT limits (PR title ≤ 100 chars, body ≤ 4000 bytes) across all repos.

**How to enable (org-level):**

1. **Organization settings** → **Actions** → **Required workflows** → **New required workflow**
2. **Source repository:** `merglbot-core/github`
3. **Workflow file:** `.github/workflows/length-check.yml`
4. **Source branch:** `main` (or tag `@v2.0.0-rc.1`)
5. **Apply to:** All repositories (or selected subset)
6. **Enforcement:** Required
7. **Save**

**Test first:**
- Start with a subset of repos or a preview branch.
- Verify no false positives before org-wide rollout.

---

## Inputs (claude-pr-assistant workflows)

| Input | Type | Default | Description |
|-------|------|---------|-------------|
| `trigger_labels` | string | `"ai-review"` | Comma-separated labels to trigger review |
| `skip_labels` | string | `"no-ai"` | Comma-separated labels to skip review |
| `model` | string | `"claude-3-5-sonnet-20241022"` | Anthropic model ID |
| `temperature` | number | `0.2` | Model temperature (0.0-1.0) |
| `max_output_chars` | number | `4000` | Max review comment length |
| `max_diff_lines` | number | `8000` | Max diff lines to process |
| `mode` | string | `"suggest"` | Stable only: `suggest` or `apply-suggestions` |

---

## Security & Best Practices

- **Fork PRs:** Steps requiring secrets skip automatically if PR is from fork.
- **Permissions:** Least-privilege (`contents: read`, `pull-requests: write`).
- **No secrets in logs:** Only secret names logged, never values.
- **Idempotent comments:** Uses `peter-evans/create-or-update-comment@v4` with `edit-mode: replace`.
- **Concurrency:** Per-PR concurrency group with cancel-in-progress.

---

## Rollout Plan (Pilot)

**Phase 1: Preview (canary)**
- Add wrapper to 2-3 pilot repos (e.g., `merglbot-core/platform`, `merglbot-public/docs`)
- Use branch ref `@mcp/ai-pr-v2-YYYYMMDD-HHMM` or tag `@v2.0.0-rc.1`
- Test: label gating, idempotence, fork safety, no spam

**Phase 2: Stable**
- After 1 week without incidents, switch to stable tag `@v2`
- Expand to more repos in waves (core → public → client orgs)

**Phase 3: Required Workflow (length-check)**
- Low-risk, high-value: enable org-wide after validation

---

## Troubleshooting

**Issue:** Workflow doesn't run
- **Check:** PR has required label (`ai-review` by default)
- **Check:** No blocking label (`no-ai`)
- **Check:** Event is `pull_request` (not `pull_request_target`)

**Issue:** "LLM not configured" in comment
- **Check:** Secret `ANTHROPIC_API_KEY` exists in repo/org
- **Check:** Secret name does not start with `GITHUB_`
- **Check:** PR is not from fork (fork PRs skip secrets for security)

**Issue:** Multiple comments
- **Should not happen:** Workflow uses idempotent update-in-place
- **Debug:** Check workflow run logs for API errors

---

## Rollback

**Per-repo:**
- Revert PR that added wrapper, or switch wrapper to older tag/branch
- Re-enable old workflow if temporarily disabled

**Central:**
- Create hotfix tag (e.g., `v2.0.1`) and update wrappers
- Never overwrite existing tags (immutable)

**Required Workflow:**
- Temporarily disable in org settings → fix → re-enable

---

## Maintenance

- **Quarterly:** Update `actions/*` dependencies, test on preview tag, promote to stable
- **Secret rotation:** Every 90 days (org-level `ANTHROPIC_API_KEY`)
- **Monitoring:** Track failure rate, API limits, comment spam reports

---

## Related MERGLBOT Rules

- `MERGLBOT: Text Length Limits` – PR/commit message length enforcement
- `MERGLBOT: Git – Non-interactive commits` – No-editor git operations
- `MERGLBOT: GitHub Actions Security` – Secrets, permissions, fork safety

---

**Owner:** platform  
**Last updated:** 2025-10-10
