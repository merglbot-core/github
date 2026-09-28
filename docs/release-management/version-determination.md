# Release version determination

The source helper fails when the semantic-release dry run fails, times out,
exceeds its output bound or provides no recognized outcome. Only a successful
explicit no-relevant-change record permits `skip=true`; empty output, a wrong
branch and suppressed errors are not a release decision. Manual versions must
be SemVer and enter through an environment variable, never shell interpolation.
Provider output remains in memory and is not printed on failure.

The subprocess has a two-minute deadline and a one-MiB aggregate output bound.
Timeout or overflow kills its isolated process group, including ordinary
descendants; normal completion also cleans up remaining group members. This
does not prove containment of deliberately detached processes. npx may
only use an already installed package. The separate authority wiring pins Node
and the top-level semantic-release package; transitive package resolution still
requires separate provenance. The parser recognizes upstream logger outcome
records and rejects
unknown or contradictory output; changing the installed semantic-release
version requires compatibility verification.

Validate locally with `node --test tests/release-version.test.mjs` (through the
host's capped-run wrapper). Tests use synthetic provider results and do not run
semantic-release, install packages, push tags or publish releases. Protected
source delivery is separate from the minimal workflow authority change. Until
that wiring lands and a natural run is verified, the production workflow still
has its original behavior; no release or savings acceptance is claimed.

The existing CI job uses Python unittest discovery. `test_release_version.py`
invokes the Node behavior suite through that path; it fails if Node is absent,
if the suite fails or if its deadline expires. A Node-only file by itself would
not be collected by that job. No separate CI workflow or dispatch is needed.

The root is not an npm package. `.releaserc.json` restricts version analysis to
`main` and the commit-analyzer/release-notes-generator plugins, with dry-run
as the default. It does not load npm, GitHub-publish, changelog-writing or git
commit plugins. Tag and GitHub release creation remain in the existing workflow.
This prevents default npm publishing prerequisites from being mistaken for
an empty release decision. Authentication and natural-run acceptance remain
separate requirements.

An isolated local bare-repository check also exercised the real semantic-release
25.0.9 package on Node 22.23.2: an initial feature commit produced version 1.0.0;
a docs-only commit after the baseline tag produced an explicit skip. Neither
dry run created a tag. This fixture is separate from the default CI unit suite,
uses no GitHub/cloud credentials and does not prove the pinned Ubuntu runtime.
