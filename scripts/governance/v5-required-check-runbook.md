# Required-check publisher preservation

The historical `v5` filenames now enforce `Merglbot PR Assistant v6`. The
wrapper adds V6 to the union of existing `contexts` and `checks[].context`;
it skips a repository only when every existing V6 binding uses gate App
**3518182**. A matching name with a missing or foreign publisher needs repair.

## Payload contract

`update-branch-protection.sh` writes the API's explicit `checks` representation,
not deprecated context names alone. For each retained non-V6 context it preserves
all distinct, known App bindings, including multiple publishers for the same
name. An existing explicit `app_id: -1` is preserved; the tool never invents an
any-publisher binding. Missing, null, malformed or unresolved publishers cause
failure before PUT. New non-V6 names require separately qualified publisher
configuration through the supported owner path; this setter does not guess an
App from recent runs. V6 is pinned to App 3518182.

Without `--check`, all existing context names are retained. With `--check`, the
list remains a replacement set; the additive wrapper passes the complete union.
The existing strict=true baseline and unrelated protection settings are unchanged.
An authorized invocation is still required for any write.

## Authority and verification

Default mode is dry-run. It writes local before/payload/after/diff artifacts under
`tmp/agent/branch-protection/` and performs no PUT. An apply remains an owner-only
branch-protection operation, requiring the live authority/receipt contract and a
fresh generated owner script. Source delivery grants do not authorize apply,
workflow dispatch, installation, activation, or protection weakening.

After a permitted PUT, the setter reads protection again and compares the exact
required context/publisher identities and strict flag with the requested payload.
A mismatch exits nonzero and is visible in the saved diff; it does not claim
success or retry/rollback automatically. The owner must qualify any recovery
against the actual state. This readback is not an atomic concurrency guarantee
and does not implement the separate BP receipt/concurrency programme.

A failed branch/protection read fails closed. Unprotected branches are skipped by
the wrapper. A live publisher-loss incident has not been demonstrated by the
source regression: GitHub may choose a recent App when a caller omits App IDs.
That behavior does not establish preservation of the prior binding.

## Offline regression

`tests/test_required_check_publishers.py` invokes both real shell entrypoints
against a temporary local `gh` stub. It covers preservation, V6 repair, duplicate
names with different Apps, fail-before-write cases, no-op behavior, and divergent
readback. It never calls real GitHub, changes a protection, or grants authority.
Repository contract CI discovers the test without a workflow change.
