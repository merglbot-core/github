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
`merglbot-public/docs/ENT_ORG_ALLOWLIST.md`. The live repo count changes with
the estate (73 non-archived, non-fork repos across 11 orgs with eligible repos
as of 2026-07-29) — the regenerated
`scripts/dependabot/ent_repository_scope.txt` is the current snapshot.

## Files Refreshed in This Repo

The weekly scope refresh PR updates exactly ONE file:

- `scripts/dependabot/ent_repository_scope.txt` — ENT downstream scope mirror
  (regenerated from the org allowlist + live per-org App scan).

Intentionally NOT touched by the scope refresh (manifest-owned surfaces;
see `tests/test_ent_scope_refresh_contract.py`):

- `scripts/pr-assistant/repo-policy-manifest.json` — curated repo policy
  manifest (includes `merglbot-core/github` as `canonical_self`); new repos
  are enrolled by policy decision, not by org scan.
- `scripts/pr-assistant/target-repos.txt` — compatibility artifact rendered
  from the manifest via `repo-policy-manifest.py sync-target-repos --write`
  and enforced by the `verify-manifest` CI gate.
- `scripts/pr-assistant/baselines/<date>/repo-policy-coverage-baseline.json` —
  rollout coverage baseline used by `rollout-audit`.

## Validation

Before merging a scope refresh PR, the following must hold:

- `python3 scripts/pr-assistant/repo-policy-manifest.py verify` reports OK.
- `rollout-audit` workflow passes against the refreshed baseline.
- `scripts/dependabot/ent_repository_scope.txt` matches the live org scan
  (73 repos across 11 orgs with eligible repos as of 2026-07-29; the exact
  count changes with the estate — the fail-closed resolver plus the >40%
  shrink guard in the workflow are the invariants, not a frozen number).
- Manifest/target counts are validated by their own `verify-manifest` gate
  (`repo-policy-manifest.json` remains the SSOT for pr-assistant enrollment;
  `merglbot-core/github` stays excluded from downstream targets and retained
  in the manifest as `canonical_self`).
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
