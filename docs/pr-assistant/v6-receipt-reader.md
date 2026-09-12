# V6 receipt reader for enterprise Dependabot closeout

The enterprise closeout caller explicitly selects `--assistant-version v6`.
The verifier reads the current PR head, all canonical App check suites and
every suite check-run page with `filter=all`, including superseded rounds.
The combined commit/check-runs endpoint has a 1000-suite ceiling and cannot
by itself establish completeness. It selects the highest check ID
for `Merglbot PR Assistant v6` produced by App 3518182. A newer pending or
failed canonical round cannot be replaced by an older approval.

The reader requires a successful completed check, unique parseable V6
summary markers, schema 1, matching repository/PR/head, zero actionable
findings, no provider degradation, `safe_to_merge`, a V6 run ID and at least
one produced engine verdict (`pass` or `fail`) under the canonical V6 reader
contract. The trusted gate enforces required engine coverage for its review
mode; legitimate lightweight single-engine approvals remain eligible.
An approved verdict with a produced engine `fail` blocks as an internal
consistency contradiction, even with zero reported findings.
Partial, changing or malformed page inventories,
duplicate markers and head changes during collection fail closed. GitHub
errors produce a bounded DATA_GAP code without emitting response bodies.
Before returning evidence, the reader compares two complete inventories and
re-fetches the selected check, including its full output. A same-head receipt
update or a newly observed round invalidates this read without retrying it.
Sequential API reads cannot make a later merge atomic; final required-check
and authority validation remains the caller's responsibility.

This migration does not require obsolete Actions run URLs, documentation
markers absent from V6, or legacy `github-actions[bot]` issue comments.
Explicit v3/v4/any callers and their defaults retain their legacy behavior.

`ok` is review evidence, not a merge authorization. The caller must separately
revalidate authority, OWNER_HOLD, every required check and the exact current
head before mutation. This script performs reads only. It does not trigger
reviews, publish receipts, change policy or merge PRs. Comment-trigger policy
and the copied final-readiness workflows are separate remaining acceptance
work under github#763/#774 and V6A E8; this source change does not close them.

Validation: `python3 -m unittest discover -s tests -p 'test_v6_review_receipt.py' -v`
and the existing verifier/enterprise-closeout self-tests. Synthetic fixtures
never produce trusted GitHub checks or authorizations.
