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
