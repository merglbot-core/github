# Release version determination

The source helper fails when the semantic-release dry run fails, times out,
exceeds its output bound or provides no recognized outcome. Only a successful
explicit no-relevant-change record permits `skip=true`; empty output, a wrong
branch and suppressed errors are not a release decision. Manual versions must
be SemVer and enter through an environment variable, never shell interpolation.
Provider output remains in memory and is not printed on failure.

The subprocess has a two-minute deadline and a one-MiB output bound; npx may
only use an already installed package. The existing package installation policy
is unchanged. The parser recognizes upstream logger outcome records and rejects
unknown or contradictory output; changing the installed semantic-release
version requires compatibility verification.

Validate locally with `node --test tests/release-version.test.mjs` (through the
host's capped-run wrapper). Tests use synthetic provider results and do not run
semantic-release, install packages, push tags or publish releases. Protected
source delivery is separate from the minimal workflow authority change. Until
that wiring lands and a natural run is verified, the production workflow still
has its original behavior; no release or savings acceptance is claimed.
