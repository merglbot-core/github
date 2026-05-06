---
title: "PR Assistant v4 Evidence Canary Operator Guide"
summary: "Operator runbook for enabling, disabling, and rolling back the disabled-by-default v4 evidence canary."
owner: "platform"
last_updated: "2026-05-08"
status: "active"
---

# PR Assistant v4 Evidence Canary Operator Guide

This document covers operator-facing controls for the disabled-by-default
`merglbot-pr-assistant-v4-evidence-canary` workflow that ships with this PR.
The canary publishes evidence-only check runs and PR comments under a
non-blocking name and exists so the v3 → v4 cutover can be staged safely
without touching default review semantics.

## Trigger and gating

- **Workflow file:** `.github/workflows/merglbot-pr-assistant-v4-evidence-canary.yml`
- **Trigger:** `issue_comment` only (`@merglbot review-v4`).
- **Hard kill switch:** the repo variable `MERGLBOT_PR_ASSISTANT_V4_CANARY_ENABLED`
  must be `true`. Default is unset, which keeps the workflow disabled.
- **Approval receipt:** `policy_approval_receipt` step writes a non-secret
  digest to `$GITHUB_OUTPUT`; only the digest is emitted to PR comments.
- **Permissions footprint:** least-privilege. `contents: read`,
  `pull-requests: read`, plus `checks: write` and `issues: write` only on the
  publish job. No `contents: write`.

## Enabling the canary on a repo

1. Confirm the repo is in the v4 staging allowlist (see
   `merglbot-public/docs/pr-assistant/v4-canary-operations.md`).
2. As a repo admin, set the variable:
   `gh variable set MERGLBOT_PR_ASSISTANT_V4_CANARY_ENABLED --body=true`.
3. Comment `@merglbot review-v4` on a non-fork PR. Forked PRs are rejected
   by `expected_head_sha` validation.
4. Verify the new check run named `Merglbot PR Assistant v4 (canary)`
   appears as **non-required**. Required checks remain v3 until cutover.

## Outputs and where they appear

- **Check run:** `Merglbot PR Assistant v4 (canary)`. Non-required, advisory.
- **PR comment:** `## Merglbot PR Assistant v4 (canary)` with a single hidden
  HTML marker block; safe for public PRs.
- **Audit:** all canary runs are logged in the workflow run history; no
  side-effects on PR labels, milestones, or assignees.

## Rollback

To disable the canary immediately on a repo:

```
gh variable set MERGLBOT_PR_ASSISTANT_V4_CANARY_ENABLED --body=false
```

Future `@merglbot review-v4` comments will be ignored (the gate fails
closed). Existing canary check runs remain in history and can be ignored;
no removal is required because they are non-required.

To roll back at the platform level (all repos), revert this PR. v3 review
behaviour does not depend on any canary state.

## Owners and escalation

- **Workflow + script owner:** Platform team (this repo).
- **Variable owner:** Repo admins.
- **Incident escalation:** open an issue in `merglbot-core/github` with
  label `pr-assistant-v4-canary` and link the affected workflow run.

## Related references

- `.github/pr-assistant-v4-canary.json` — staging allowlist + receipt schema.
- `merglbot-public/docs/pr-assistant/v4-canary-operations.md` — broader v4
  rollout policy in the public docs SSOT.
- `scripts/pr-assistant/pr-assistant-v4-evidence-canary.py` — canary
  enforcement script (validates config, gates, and emits the receipt).
