# Runbook: v5 Required-Check enforcement

Enforces `Merglbot PR Assistant v5` as a required status check across the
App-installed orgs, via `apply-v5-required-check.sh` (wrapping the canonical
`update-branch-protection.sh`) and the `v5-required-check-sync.yml` workflow.

## Who may apply
- **Dry-run / drift report**: anyone with read; runs on the weekly cron and fails
  if any protected repo is missing v5.
- **`--apply` (mutation)**: only via `workflow_dispatch` with `apply=true`, which
  requires write/dispatch on `merglbot-core/github`, OR a maintainer running the
  script locally with an admin-scoped token. Bulk branch-protection changes are
  **never** automatic — the cron path is dry-run only.

## Token / IAM
- Uses `secrets.ENTERPRISE_GITHUB_TOKEN` (the same enterprise token the other ENT
  governance workflows use). It needs `administration:write` on the target orgs
  (branch-protection PUT). Store in repo/org secrets only; rotate per the ENT
  secret-rotation policy; never echo the value (names only).
- Blast radius is high (cross-org branch protection) — prefer scoping a future
  `environment:` with required reviewers on the apply path.

## What it does / preserves
- Computes `existing required checks ∪ "Merglbot PR Assistant v5"` per repo, so it
  is **additive** and **idempotent** (repos already requiring v5 are skipped).
- The setter preserves every other setting: `enforce_admins`, required reviews,
  restrictions, bypass allowances, and all other required status checks. Verified
  live on `merglbot-extractors/ga4-extractor` (enforce_admins=true preserved; the
  3 existing checks kept, v5 appended).
- Repos with **no branch protection** (`.protected=false`) are skipped (logged) —
  never force-created. A failure to *read* a branch fails CLOSED (counted, non-zero
  exit), never a silent skip.
- `Merglevsky-cz` + `lrtch` are excluded until the App is installed there.

## Dry-run
```
scripts/governance/apply-v5-required-check.sh --org merglbot-core --dry-run
```
Reports `WOULD-ADD` (protected repo missing v5), `OK` (already), `SKIP` (no BP).

## Apply
```
scripts/governance/apply-v5-required-check.sh --org merglbot-core --apply --yes
```
or dispatch `v5-required-check-sync.yml` with `apply=true`.

## Rollback
A wrongly-added context can be removed by re-running `update-branch-protection.sh`
for the repo with the desired `--check` set **excluding** v5 (it replaces the
context list). For an `enforce_admins=true` repo where v5 is wedging a merge, use
the owner break-glass (temporary `enforce_admins=false` → merge → restore), never a
permanent protection weakening.

## Audit
`apply-v5-required-check.sh` prints a per-repo `ADDED/OK/SKIP/ERROR` line and a
summary; the setter writes before/after/diff artifacts under
`tmp/agent/branch-protection/<date>/<ts>/` (gitignored).
