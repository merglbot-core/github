---
title: "ENT Dependabot Autonomous Closeout"
summary: "Weekly GitHub Actions control plane for evidence-gated Dependabot PR merge/closeout across the Merglbot ENT repository scope."
owner: "platform"
status: "active"
---

# ENT Dependabot Autonomous Closeout

`ENT Dependabot Weekly Closeout` runs every Sunday at `13:00 UTC` and calls
`ENT Dependabot Autonomous Closeout`, the reusable workflow that scans the
canonical 42-repo Merglbot ENT scope from `merglbot-public/docs/REPOSITORY_MAP.md`.

## Canonical SSOT Dependencies

This repo-local guide describes the implementation in `merglbot-core/github`.
Platform policy authority remains in `merglbot-public/docs`:

- `ENT_DEPENDABOT_AUTONOMOUS_CLOSEOUT.md` defines the canonical weekly lane
  contract, including lockfile/simple dependency-only scope and mixed-manifest
  fail-closed behavior.
- `SECURITY.md` defines the security boundary for current-head-safe scope
  classification and no stale scope proof reuse.
- `MERGLBOT_GITHUB_ACTIONS_GLOBAL_RULES.md` defines safe workflow input handling
  through `env:` and the read-only classify -> act -> re-verify pattern for
  autonomous workflow mutations.

## Runtime Contract

- `dry-run` scans and classifies Dependabot PRs without GitHub writes.
- `apply` may close irrelevant Dependabot PRs, align bounded branch protection,
  and squash-merge PRs that satisfy every current-head gate.
- Default merge eligibility is limited to lockfiles and simple dependency-only
  metadata files. Mixed-purpose manifests such as `package.json`,
  `pyproject.toml`, `pom.xml`, `build.gradle(.kts)`, `go.mod`, or `global.json`
  are blocked until a content-aware validator proves dependency-only hunks.
  Dependabot PRs touching `.github/workflows/**`, reusable workflow wiring,
  Dockerfiles, Terraform, deploy config, auth/IAM, secrets, runtime bootstrap,
  or data/schema promotion surfaces are blocked for human checkpoint unless a
  narrower canonical allowlist covers that exact file class.
- Stale age alone is never a close reason.
- The workflow does not deploy, run Terraform apply, mutate secrets, change
  default branches, or bypass branch protection.

## Slack Telemetry

The reusable workflow posts a compact run summary to Slack when
`slack_notify=true` and the `SLACK_DEPENDABOT_WEBHOOK_URL` secret is configured
as a GitHub Actions repository secret in `merglbot-core/github`, or as an
organization secret explicitly scoped only to `merglbot-core/github`. The secret
value must never be printed or embedded in logs; channel routing is controlled by
the Slack webhook configuration.

Slack messages include the run status, scanned repo count, merged/closed/blocked
Dependabot PR counts, remaining Dependabot and non-Dependabot PR totals, open
issue totals, top blocker reasons, and the GitHub Actions run URL. The JSON
artifact remains the authoritative receipt if Slack delivery fails. Slack is
best-effort for the weekly workflow: missing Slack configuration is recorded as
`not_configured`, POST failure is recorded as `telemetry_degraded`, and neither
case may cause automatic PR/issue mutation retries.

Manual apply validation can pass a `pr_allowlist` plus `approval_note` or
`approval_issue_url`; the workflow records those values in the receipt and only
acts on the allowlisted PRs. `pr_allowlist` accepts comma- or whitespace-separated
`owner/repo#number` tokens or GitHub PR URLs. For
`post_change_validation=true`, the workflow fails closed unless all of the
following hold: `pr_allowlist` is non-empty or the approval material explicitly
contains `approval_scope=full_queue`; `approval_note` or `approval_issue_url` is
present; the approval material covers the current workflow SHA or run ID; and
the approval material contains `expected_action=`. If `approval_issue_url` is
used, the referenced packet must contain the approval scope, expected action, and
covered workflow SHA or run ID in a durable form. All required markers must
appear in one coherent approval packet: the `approval_note`, the referenced
issue body, or a single referenced issue comment. Markers spread across multiple
historical comments do not satisfy the authorization gate. `authorized_sha` or
`authorized_run` must match the current workflow SHA or run ID, and approval
packets loaded from `approval_issue_url` must come from a trusted approval repo
and a trusted GitHub author whose login matches `approved_by`. Approval material
must also record approver identity, timestamp, approved scope, and expected
action per PR.

## Merge Gate

Every merged Dependabot PR must prove:

- the changed files are manifest/lockfile-only dependency metadata,
- required checks are green on the live head,
- the latest Merglbot PR Assistant receipt is current-head and
  `approved_for_closeout`,
- Cursor Bugbot has a current-head pass when available, or the receipt records
  that Cursor was absent/neutral/skipping and not required,
- the merge uses squash with `--match-head-commit`.

## Policy Alignment

If required human review is the only blocker for an otherwise evidence-gated
Dependabot PR, the workflow may align the review gate only when the target
ruleset is provably scoped to Dependabot-authored PRs. Repository-wide review
requirements must not be lowered for this lane; if GitHub cannot prove scoped
alignment, the PR is classified as `BLOCKED_POLICY`.

## Artifacts

Each run writes:

- `ent_dependabot_weekly_receipt.json`
- `ent_dependabot_repo_results.json`
- `summary.md`
- branch-protection snapshots under `policy/` when alignment is evaluated

The weekly caller posts the human summary and machine receipt to the cleanup
steady-state tracking issue.
