# Canonical container provenance action

This source component generates the existing bound statement and optional
single-platform projection with canonical SHA-256 values. It preserves the
declared predicate and build type; it does not certify a SLSA level or provide
a cryptographic signature. Caller metadata is passed through environment values
and JSON serialization, never inserted into a shell program.

The full and platform JSON outputs retain the reusable workflow's existing
output names. Additional path outputs identify files created in an exclusive
runner temporary directory, avoiding stale files from the caller checkout.
The action always rejects a missing or malformed candidate digest. A caller
that performs a non-pushing build with no manifest must skip both this action
and its artifact upload. Pushing builds must still invoke validation.

Landing order: merge and verify this source plus its tests through exact-head
V6 first. A separate minimal protected workflow change must reference the
verified source merge commit, pass inputs, preserve the nonpush/fail-closed
conditions and upload only returned paths. No placeholder pin is deployable.
Then verify authorized caller adoption and a natural produced artifact.
Neither this source nor its local tests activate a workflow, change a caller
pin, authorize a protected merge or prove runtime/financial acceptance.
