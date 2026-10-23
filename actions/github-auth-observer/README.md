# GitHub authentication observer

Source-only Project 39 diagnostic. Adding this action does not instrument or run
any workflow. Protected caller workflow changes and credential selection require
their own approved delivery; no new credential, App key, IAM or activation is
introduced here. Refs merglbot-core/github#812.

Run only in an approved trusted GitHub.com main-branch `push`, `schedule` or
`workflow_dispatch` job on an Ubuntu runner with Python 3 and `/usr/bin/gh`.
The caller must supply exactly the intended existing credential as `GH_TOKEN`
in step environment. Ambient `GITHUB_TOKEN` is rejected. No credential is minted,
rotated, hashed, printed, passed on argv or resolved from another source. The gh
child gets an empty temporary configuration and a closed environment. Python runs
in isolated mode so ambient Python import configuration cannot replace modules.

At most three GET requests are made, without retries: `rate_limit`, `user`, and
only after a 403 from `user`, `installation/repositories?per_page=1`. No search,
repository content, mutation or arbitrary endpoint is available. Stop before
identity probes if the core snapshot has fewer than 2,003 remaining requests,
preserving a 2,000-request reserve for this bounded probe. This is a snapshot;
other concurrent consumers can still spend the shared budget. It is not a
distributed reservation or protection against secondary limits.

The action returns a mode-0600 JSON file under a new mode-0700 runner directory
via `report_path`, including on diagnostic failure. It does not upload artifacts
or print the report. Only projected counters and endpoint status are retained;
user IDs/logins, repository names, provider error bodies and credential values
are discarded. A numeric CLI HTTP error is a diagnostic classification, not a
credential attestation. Inconclusive observations fail the step.

`user_context` does not distinguish PAT, OAuth or App user tokens and does not
identify the account. `installation_endpoint_accepted` is deliberately weaker
than verified installation identity: public accessibility, upstream behavior and
the configured credential must be checked independently. Neither the installation
ID, actual repository selection nor a complete budget owner is inferred. The
GraphQL counter is an endpoint snapshot, not a successful GraphQL operation.

Every report keeps `credential_selection_proven: false` and
`migration_accepted: false`. An external verifier must bind the actual immutable
caller workflow, secret reference, run/job/attempt, artifact hash, observation
and trusted identity configuration before changing inventory or retirement state.
A source merge, successful action step or this JSON alone cannot establish
production migration, higher limits, PAT retirement or 168-hour acceptance.

References: [installation endpoint](https://docs.github.com/en/rest/apps/installations#list-repositories-accessible-to-the-app-installation),
[GitHub CLI API](https://cli.github.com/manual/gh_api).
