# ⚠️ DEPRECATED: Claude PR Assistant v2

> **This document is deprecated.** PR Assistant v3 is now the standard.
> See [MERGLBOT_PR_ASSISTANT_V3.md](https://github.com/merglbot-public/docs/blob/main/MERGLBOT_PR_ASSISTANT_V3.md)

---

## Quick Start (v3)

On any PR in any Merglbot repository, comment:

```
@merglbot review
```

This triggers a review-only multi-model review using **`claude-opus-4-6` + `gpt-5.4` (`reasoning_effort=high`)** with **final synthesis on OpenAI `gpt-5.4` (`reasoning_effort=high`)**.

Review output is intentionally **review-only**. Closeout remains a separate handoff path and final merge stays **`human_merge_only`**.

The review comment now also carries advisory docs metadata:
- `docs_follow_up_hint`: `likely_required`, `not_observed`, or `none`
- `suggested_docs_targets`: JSON array of advisory repo-relative targets for `merglbot-public/docs`, or `[]`
- `docs_signal_basis`: always `review_output_only`

These fields are soft review metadata only. They do not add a required check, they do not create a merge blocker, and `documentation_obligation_state` remains non-authoritative review output rather than docs-classifier truth.

The review comment must also carry a visible **Merglbot Review Receipt** and
matching hidden markers so autonomous closeout can prove current-head review
truth. Required markers are:

- `MERGLBOT_REVIEW_RECEIPT_SCHEMA_VERSION`
- `MERGLBOT_REVIEW_HEAD_SHA`
- `MERGLBOT_REVIEW_VERDICT`
- `MERGLBOT_REVIEW_STATUS`
- `MERGLBOT_PR_CHECK_SURFACE`
- `MERGLBOT_RUN_ID`
- `MERGLBOT_RUN_URL`

Use `scripts/pr-assistant/verify-review-receipt.py --repo <owner/repo> --pr
<number>` to emit the JSON verifier contract for closeout lanes. The verifier
also checks that `MERGLBOT_RUN_ID` belongs to the PR Assistant workflow path and
validates `MERGLBOT_RUN_URL` against the PR URL host, so the contract works on
GitHub Enterprise hosts without hard-coding `github.com`. `ok=true` is reserved
for current-head `status=success` with `verdict=approved_for_closeout`; blocked
or failed receipts remain parseable evidence but are not merge approval.

The PR Assistant receipt is intentionally fail-closed for documentation
authority. If the generated review does not explicitly report
`documentation_obligation_state=satisfied` or
`documentation_obligation_state=not_required`, the workflow must emit
`MERGLBOT_REVIEW_VERDICT=blocked_missing_authority` instead of approving
closeout. The only normalization fallback is the explicit review section
`SSOT Sync (Docs)` containing `None`; that machine-normalizes an otherwise
missing `Documentation Obligation State` field to `not_required` because the
review already made the no-docs-obligation claim visible. If the machine field
is present but invalid, the workflow must keep the fail-closed `unknown` state
instead of treating it as `not_required`. The metrics artifact mirrors the same lowercase
`review_receipt.verdict` value that appears in the visible review receipt and
hidden markers, while the legacy top-level `verdict` field remains available
for historical dashboards.

For lighter review: `@merglbot review --light`

---

## Current Workflow Location

- **Canonical source**: `merglbot-core/github/.github/workflows/merglbot-pr-assistant-v3-on-demand.yml`
- **Deployed copy** (per target repo): `.github/workflows/merglbot-pr-v3-on-demand.yml` via `scripts/pr-assistant/deploy-v3.sh`
- **Inventory policy**: `scripts/pr-assistant/repo-policy-inventory-policy.json`
- **Coverage SSOT**: generated `scripts/pr-assistant/repo-policy-manifest.json`
- **Coverage baseline**: `scripts/pr-assistant/baselines/2026-03-29/repo-policy-coverage-baseline.json`
- **Scope sync automation**: `.github/workflows/merglbot-pr-assistant-manifest-sync.yml`

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
