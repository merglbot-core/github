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
- Default merge eligibility uses `validator_profile=maximum_autonomy_v2`.
  Lockfile-only updates remain merge-eligible. `package.json` is merge-eligible
  only when the validator proves that the diff changes dependency version ranges
  under `dependencies`, `devDependencies`, `optionalDependencies`, or
  `peerDependencies` and a sibling lockfile changed in the same PR.
- `.github/workflows/**` remains sensitive by default, but Dependabot PRs that
  only bump `uses:` refs to the same action/reusable workflow target can be
  treated as `VALIDATED_WORKFLOW_REF_ONLY`. Trigger, permission, env, shell,
  secret, deploy, matrix, conditional, or job topology changes remain blocked.
- Terraform, Dockerfiles, deploy config, auth/IAM, secrets, runtime bootstrap,
  or data/schema promotion surfaces are blocked for human checkpoint unless a
  narrower canonical allowlist covers that exact file class.
- Stale age alone is never a close reason.
- Deprecated, obsolete, empty, or superseded PRs may be auto-closed only when
  the current run proves strong evidence: empty diff, dependency/path absent on
  the base branch, base branch already carrying the same or newer dependency
  version, or a newer sibling Dependabot PR for the same dependency/path.
  Version-range specifiers such as `^`, `~`, `<`, `>`, or pre-release tags do
  not prove that the update is already carried on `main`; those cases fail
  closed. Sibling supersession also requires an unambiguous matching path hint,
  so monorepo PRs are not closed solely by dependency name.
  Close comments must include evidence, successor/main proof when applicable,
  workflow run URL, and a reopen condition.
- The workflow does not deploy, run Terraform apply, mutate secrets, change
  default branches, or bypass branch protection.
- Cross-org GitHub API authority should come from the GitHub App secrets
  `ENT_DEPENDABOT_APP_ID` and `ENT_DEPENDABOT_APP_PRIVATE_KEY`. The engine mints
  short-lived installation tokens per repository owner and fails closed when the
  app is not installed for a target owner. `ENTERPRISE_GITHUB_TOKEN` remains only
  a legacy fallback for non-ENT/single-owner tests.
- Missing or stale Merglbot evidence is remediated through the target repo's
  active `Merglbot PR Assistant v3 (On-Demand Multi-Model)` workflow via
  `workflow_dispatch` on the PR head ref with `expected_head_sha`. The legacy
  `@merglbot review --light` comment path is not used by the ENT weekly apply
  lane because GitHub App comments do not carry a trusted author association.
- When `autonomous_fix_loop=true`, current-head Merglbot `changes_required`,
  Cursor blockers, and real CI failures are not treated as final closeout
  blockers in dry-run. They are classified as `WOULD_START_AUTONOMOUS_FIX_LOOP`
  or `WOULD_HEAL_REQUIRED_CHECKS` with a findings ledger and the expected
  prompt-library close-loop contract. The write-capable fix loop is deliberately
  a separate orchestrator lane: it may push only minimal commits to the existing
  Dependabot branch, must rerun Merglbot/Cursor/current-head gates after every
  new head, and may merge only through squash `--match-head-commit` after
  post-fix approval. This follows `Autonomous PR Close-Loop v1` and
  `MERGLBOT_PR_REVIEW_AUTONOMOUS_AUTOMERGE_V1`.
- Behind PRs are updated through GitHub's pull request `update-branch` API with
  `expected_head_sha`. The lane no longer relies on `@dependabot rebase`
  comments, because Dependabot rejects that command from actors without push
  access semantics. After any update-branch change, every gate is recomputed on
  the new head.
- Installation tokens are minted per repository owner and retried once on
  `401 Bad credentials` after invalidating the owner token cache. A second 401
  is classified as an installation/capability blocker, not silently retried
  indefinitely.
- Local `single_repo` diagnostics validate against the repo-local
  `scripts/dependabot/ent_repository_scope.txt` mirror to stay inside the
  canonical 42-repo boundary without unnecessary cross-repo auth. GitHub Actions
  `single_repo` runs validate against canonical remote `REPOSITORY_MAP.md` on
  `main`, and `all` plus multi-owner `cohort` runs require GitHub App auth.
- Canonical GitHub App setup and permission authority lives in
  `merglbot-public/docs/ENT_DEPENDABOT_GITHUB_APP_SETUP.md`; repo-local setup
  docs are implementation mirrors only.
- Secret naming and no-log policy authority lives in
  `merglbot-public/docs/MERGLBOT_SECRETS_NAMING_AND_LOGGING.md`; workflows and
  scripts may report secret names or configured/not-configured booleans, never
  secret values.

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
and a trusted GitHub author whose login matches `approved_by`. Trusted approvers
come from the explicit `ENT_DEPENDABOT_TRUSTED_APPROVERS` policy list; the
workflow actor is not trusted implicitly. Approval material must also record
approver identity, timestamp, approved scope, and expected action per PR.

Manual `workflow_dispatch` defaults to `repo_scope=single_repo` and
`single_repo=merglbot-public/docs`, so a default manual run is a safe dry-run
smoke rather than an ENT-wide execution. It exposes a bounded 10-input surface.
It includes
`approval_issue_url` and `comment_report`; when `comment_report=true`, the report
is posted to the default tracking issue `merglbot-public/docs#636`. Reusable
`workflow_call` callers keep the same default fallback when `tracking_issue` is
omitted, and can still override routing with an explicit `tracking_issue` value.
Reusable callers may pass `validator_profile`; manual dispatch uses the default
`maximum_autonomy_v2` profile to stay within GitHub's 10-input limit.
Reusable callers may also enable `autonomous_fix_loop`,
`orchestrator_fix_handoff`, `max_fix_iterations`, `max_review_iterations`, and
`fix_profile=dependabot_safe_v1` when they want a planning receipt for the
orchestrator PR close-loop waves. Those inputs are intentionally kept off manual
`workflow_dispatch` to preserve GitHub's 10-input limit and do not by themselves
perform semantic code edits inside GitHub Actions.

## Required Check Taxonomy

The orchestrator classifies required-check blockers into a canonical taxonomy.
Each abstract category maps to one or more concrete classification strings
emitted by `classify_required_check_blocker()`:

| Canonical taxonomy name | Concrete categories | Healing action |
|---|---|---|
| `rerunnable_check` | `stale_or_pending_analysis_context`, `stale_or_pending_security_context`, `pending_or_never_emits`, `skipped_analysis_context`, `skipped_or_neutral` | `diagnose_or_rerun_required_check` |
| `stale_required_context` | `stale_or_pending_analysis_context`, `stale_or_pending_security_context` | `diagnose_or_rerun_required_check` |
| `missing_workflow_enrollment` | blocker string `repo_enrollment:merglbot_workflow_dispatch_missing` | Fail closed; separate enrollment lane |
| `real_ci_failure` | `check_failed_real` | `start_minimal_pr_branch_fix_loop` |
| `policy_required_but_never_emits` | `pending_or_never_emits` | `diagnose_or_rerun_required_check` |

`rerunnable_check` is a superset: any category whose healing action is
`diagnose_or_rerun_required_check`. `stale_required_context` is the subset
that specifically represents analysis/security checks that were once required
but are now pending or stale due to branch-protection drift. The
`missing_workflow_enrollment` category is not a check-state classification but
a Merglbot-dispatch blocker recorded when the target repo lacks the expected
review workflow. `policy_required_but_never_emits` captures checks that are
listed in branch protection but have no workflow or app configured to ever
emit a status for the PR's commit.

## Merge Gate

Every merged Dependabot PR must prove:

- the changed files are lockfile-only, `VALIDATED_MANIFEST_DEP_ONLY`, or
  `VALIDATED_WORKFLOW_REF_ONLY`,
- required checks are green on the live head,
- the latest Merglbot PR Assistant receipt is current-head and
  `approved_for_closeout`; if the receipt was missing or stale, the closeout
  engine must have triggered a head-bound `workflow_dispatch` review and then
  verified the emitted receipt markers,
- Cursor Bugbot has a current-head pass when available, or the receipt records
  that Cursor was absent/neutral/skipping and not required,
- the merge uses squash with `--match-head-commit`.

If a close-loop lane pushed a fix commit, the final merge gate must additionally
prove that the newest Merglbot receipt covers the post-fix head and that the
stale-findings ledger was closed by a new head or a documented false-positive
rationale. Same-head "no new findings" is not sufficient.

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

Per-PR receipts include the Merglbot dispatch method/ref/head SHA when a review
dispatch was needed, and include update-branch API evidence when a PR started
behind its base branch. v2 receipts also include `validated_scope_class`,
`scope_validator_evidence`, `would_dispatch_merglbot_review`,
`would_update_branch`, `superseded_by`, and `close_reopen_condition` when
applicable. Remaining-queue v2 receipts also include
`required_check_diagnostics`, separating pending/skipping context drift from
real failed checks before any human or admin-healing lane acts on them.

The weekly caller posts the human summary and machine receipt to the cleanup
steady-state tracking issue.
