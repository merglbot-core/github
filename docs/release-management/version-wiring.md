# Release version helper wiring

This minimal workflow authority change depends on the separately reviewed and
protected-merged `scripts/release/determine-version.mjs` helper. This document
is delivered with the reviewable source; the workflow-only activation is a
separate dependent PR. Land the source
first and revalidate the dependency on actual main before opening or merging
this wiring. Passing local fixtures is not V6 review, activation or a natural
release run. This change preserves triggers, permissions and release/notify jobs.

The same minimal authority change pins Node 22.22.2 and semantic-release 25.0.9,
whose declared Node requirement includes this runtime. The old Node 20 setup
falls outside the observed semantic-release 25.0.9 engine requirements; this
is compatibility evidence, not a verified cause of a production failure.
Unused globally installed git/changelog plugins are not needed by the
helper's explicitly selected analysis plugins. No auto-discovered root
release configuration is introduced by the source PR.
Top-level pins do not lock all transitive dependencies. Verify natural runner
compatibility and release outcome after protected landing; local macOS tests
are not an Ubuntu runner or production release proof.
