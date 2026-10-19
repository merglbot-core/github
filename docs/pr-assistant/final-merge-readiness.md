# Final Merge Readiness Policy (retired)

**Status: retired 2026-10-19** (merglbot-core/github#774, program "V6 autonomie (5)",
merglbot-core/platform#2448). The hub copy of the Final Merge Readiness check
(`.github/workflows/final-merge-readiness.yml`, evaluator
`scripts/pr-assistant/final-merge-readiness.py`, manifest
`.github/policies/final-merge-readiness.json`) was removed. Restore it from git
history if ever needed (`git log --diff-filter=D -- scripts/pr-assistant/final-merge-readiness.py`).

## Why

- It was V5-era: it parsed PR Assistant v4/v3 receipt **comments** and never
  recognised the `Merglbot PR Assistant v6` check-run receipt, which is the
  only active review gate.
- It had no `workflow_call` trigger and no repository called it; it only ran
  here as a path-filtered self-test and was not a required check in this repo
  (required: `ci`, `Merglbot PR Assistant v6`).
- The repo-local readiness copies raced the V6 review: they read the receipt
  once per `pull_request` event while V6 publishes minutes after the push.
  merglbot-denatura/acquisition-analysis measured 51 failures / 27 manual
  re-runs in 100 runs and folded the job into `ci` with **no receipt wait and
  no validator call** (merglbot-core/github#958, merglbot-core/platform#1722).
  merglbot-proteinaco/acquisition-analysis follows the same shape under
  github#774 and keeps its required `final-merge-readiness` context reporting.

## What replaces it

Merge readiness is the required `Merglbot PR Assistant v6` check on the exact
head plus the agent merge readback of its receipt markers
(`merglbot-public/docs/MERGLBOT_PR_ASSISTANT_V6.md` §5a). A repository that
still requires a `final-merge-readiness` context keeps a job of that name that
only runs local self-tests; it must never fail because the V6 receipt has not
landed yet.

Tenant copies at retirement (repo-local, not callers of this hub file):

- `merglbot-denatura/acquisition-analysis`: `scripts/check-final-merge-readiness.mjs`
  (parser self-test inside the `ci` job).
- `merglbot-proteinaco/acquisition-analysis`: `.github/workflows/final-merge-readiness.yml`
  + `scripts/check-final-merge-readiness.mjs` (required context, self-test only).

## Policy Engine Bootstrap

`scripts/policy-engine/final_merge_readiness.py` is the stdlib-only policy
engine (modes `final-merge-readiness` and `terraform-approval`). It is out of
scope of this retirement: it also carries the `terraform-approval` mode and
its own tests, so removing it is a separate decision. It is covered by
`.github/workflows/policy-engine-tests.yml` and
`tests/test_final_merge_readiness.py`.

The bootstrap evaluator has these safety contracts:

- Runtime is Python 3.11 or newer. The workflow installs Python 3.11 and the
  script exits immediately on older interpreters.
- `repo_scope` is required in `scripts/policy-engine/policy-manifest.json`.
  Missing or malformed scope fails closed, the same as an out-of-scope
  repository owner.
- Changed-file paths are normalized before glob matching and receipt hashing,
  including Windows separators. Absolute paths, drive-prefixed paths, parent
  traversal, and changed-file lists above the policy size limit are classified
  as security-sensitive instead of being accepted.
- The module remains import-safe for unit tests; command-line execution stays
  behind the `if __name__ == "__main__"` guard.
- GitHub Actions are pinned to commit SHAs. Refresh both the checkout and
  setup-python pins in the dependency refresh lane, with policy-engine tests
  green in the same PR.
