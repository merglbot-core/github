# Prepared Copilot aggregate reader

The existing monitor reads aggregate organization Copilot seats. Its optional
`CopilotBrokerReader` can instead consume the server-only aggregate protocol
specified in
[platform#2302](https://github.com/merglbot-core/platform/pull/2302).
That draft is not a deployed service.

An explicit trusted caller constructs the reader from:

- The approved HTTPS broker origin, without port, credentials, path or query.
- An immutable tuple of `CopilotTarget` values: numeric organization ID,
  lowercase login, approved grant ID and the exact target SHA-256 accepted
  during deployment. Never automatically adopt hashes returned by discovery.
- A bounded, reviewed workload identity provider callable, `identity(audience)`.
  It must supply a valid Google ID token for the exact broker origin and
  maintain its own automatic refresh. This adapter has no credential discovery,
  login, workstation ADC, token environment lookup or alternate provider.

The caller passes the reader explicitly to
`collect_github(enterprise, orgs, pricing, copilot_reader=reader)`. Every
requested organization must be bound; duplicate organizations are refused
before identity acquisition. Each read obtains identity from the provider,
then sends only the grant ID and target hash to `/v1/copilot-billing`.
Normal TLS verification remains enabled, redirects and environment proxy/auth
configuration are disabled, and responses are streamed with a 4 KiB limit and
bounded connect/read timeouts plus a 20-second body-receive deadline.

The response must contain only the pinned organization ID/login and an integer
seat total. Wrong targets, duplicate JSON keys, additional fields, booleans,
negative/oversized totals and provider errors fail closed. No response body,
identity or provider exception is logged or returned as an error. Zero seats is
authoritative only in a valid successful envelope; 404 is not converted to zero.
The monitor's resulting report shape and configured Copilot unit-price
calculation are preserved.

Selected broker mode never calls the legacy personal credential path, even
when personal environment variables are present or the broker fails. The
existing legacy mode is unchanged until an explicit reviewed cutover.
Nonzero Enterprise Cloud seat pricing is rejected in broker mode before any
network call: that branch needs separate nonpersonal enterprise/member
operations and cannot silently use the old token. Copilot unit prices must be
finite and nonnegative.

## Remaining activation work

This source provides the reader and the actual monitor integration seam;
the CLI and scheduled workflow still use legacy mode. Activation needs the
reviewed shared workload-identity wiring, exact trusted target configuration,
the deployed/accepted broker, App permissions, complete cost authorization and
an owner-reviewed workflow step where applicable. Do not paste an ID token
into settings or claim that this optional Python parameter completed a cutover.

Before retirement, prove the real scheduled job with inherited PAT variables
absent, actual installation identity/limit, renewal, safe failures, correct
report totals and natural post-retirement runs. Local HTTP tests and source
integration against a fixture broker are not production acceptance.
