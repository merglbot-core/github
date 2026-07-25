---
title: "ENT Scope Refresh"
summary: "How the ENT scope mirror in this repo is refreshed against the canonical SSOT."
owner: "platform"
status: "active"
---

# ENT Scope Refresh

This document explains the intent and validation of the periodic ENT scope
refresh PRs against the canonical SSOT.

## Canonical SSOT

The platform-wide canonical scope is defined in
`merglbot-public/docs/REPOSITORY_MAP.md` and
`merglbot-public/docs/ENT_ORG_ALLOWLIST.md`. The current SSOT is **46 active
repositories across 11 active organizations** (see RULEBOOK and the docs index
for the latest snapshot).

## Files Refreshed in This Repo

A scope refresh PR updates the locally mirrored scope artifacts so the
PR Assistant rollout-audit and ENT Dependabot lane operate against current
truth:

- `scripts/dependabot/ent_repository_scope.txt` — ENT downstream scope mirror.
- `scripts/pr-assistant/repo-policy-manifest.json` — managed repo policy
  manifest (includes `merglbot-core/github` as `canonical_self`).
- `scripts/pr-assistant/target-repos.txt` — copy-deploy target list rendered
  from the manifest.
- `scripts/pr-assistant/baselines/<date>/repo-policy-coverage-baseline.json` —
  rollout coverage baseline used by `rollout-audit`.

## Validation

Before merging a scope refresh PR, the following must hold:

- `python3 scripts/pr-assistant/repo-policy-manifest.py verify` reports OK.
- `rollout-audit` workflow passes against the refreshed baseline.
- Counts match the canonical SSOT: manifest 46, downstream target 45,
  ENT scope 45, with `merglbot-core/github` excluded from downstream and
  retained in the manifest as `canonical_self`.
- All Merglbot/CI/secret-scanning checks are terminal green on the exact head.

## Supersession

A new scope refresh PR supersedes any earlier still-open scope refresh PRs.
Prior duplicates should be closed with `CLOSED_WITH_EVIDENCE` once the new PR
is opened or merged.

## Cross-References

- `docs/ent-dependabot-autonomous-closeout.md` — weekly Dependabot lane that
  consumes the refreshed ENT scope mirror.
- `merglbot-public/docs/REPOSITORY_MAP.md` — canonical platform scope SSOT.
- `merglbot-public/docs/ENT_ORG_ALLOWLIST.md` — canonical ENT org allowlist.
