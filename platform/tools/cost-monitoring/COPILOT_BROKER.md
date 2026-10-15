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

## Explicit CLI and renewable Actions identity

The opt-in CLI flag is `generate --github-broker-config <approved-json>`.
The flag is absent by default. It composes the actual collector with the
aggregate reader, using the public Google Auth SDK; it does not download the
internal platform repository, enable private Actions sharing or publish its
implementation. The public cost-monitor repository cannot use the proposed
internal platform action under the current access policy.

The nonsecret JSON fixes the origin, approved WIF provider, target service
account and numeric subject, immutable repository/owner IDs, main-branch
workflow context and accepted organization targets. Unknown/duplicate fields,
other events, forks, changed IDs or another workflow fail before identity use.
The allowed events are exactly schedule and workflow_dispatch on main.
This example is synthetic and is not a deployment configuration:

```json
{
  "version": 1,
  "origin": "https://broker.example.invalid",
  "identity": {
    "service_account": "copilot-reader@runtime-project.iam.gserviceaccount.com",
    "subject": "123456789",
    "provider": "projects/123456/locations/global/workloadIdentityPools/actions/providers/github",
    "repository": "merglbot-core/github",
    "repository_id": "42",
    "repository_owner_id": "101",
    "ref": "refs/heads/main",
    "workflow_ref": "merglbot-core/github/.github/workflows/cost-monitoring.yml@refs/heads/main"
  },
  "targets": [
    {
      "organization_id": 101,
      "organization_login": "core",
      "grant_id": "core-total",
      "target_sha256": "0000000000000000000000000000000000000000000000000000000000000000"
    }
  ]
}
```

A separately reviewed step of the existing first-party Google authentication
action, pinned at `7c6bc770dae815cd3e89ee6cdf493a5fab2cc093`, must create the
external-account file for this exact identity. Pass its explicit
`credentials_file_path` output as `GITHUB_ACCESS_WIF_CONFIGURATION` to the
generate step. Use `export_environment_variables: false` for that additional
auth step so it does not replace the existing GCP/BigQuery credentials.
The configured broker identity is distinct from the existing billing reader.
Remove the personal enterprise-token environment binding when activating the
broker path; the ordinary Actions run token can retain its separate notifier
purpose.

The identity provider accepts only an owned, private (0600), single-link regular
`gha-creds-*.json` file directly under GITHUB_WORKSPACE, with the exact schema
generated by the pinned action. It verifies the WIF audience, fixed Google STS
and IAM endpoints, target service account, runner OIDC URL/audience and
job-provided request identity. Service-account key files, authorized-user
credentials, endpoint overrides, executable credential sources and default
credential discovery are rejected. Never commit, print, upload or paste the
generated file; it contains transient runner authentication material.

Each renewal retrieves a fresh signed Actions assertion, uses Google's public
identity-pool SDK to exchange it, then calls the fixed Google generateIdToken
operation through its authenticated session. The source WIF identity directly
invokes that operation; no extra generateAccessToken chain is activated.
The configured WIF principal needs the reviewed getOpenIdToken permission on
the exact target account, and that account needs the separate broker invoker
and policy grant. This source does not create those bindings.

The returned Google audience, numeric subject, email and expiry must match.
Google verifies the Actions signature/WIF authorization and the broker
independently verifies the Google signature; local parsing is not authorization.
One in-process lane caches the ID token only outside its 300-second refresh
margin, rejects a backward wall-clock cache hit and renews from the first-party
file. Neither a one-time action ID-token output nor a personal credential is a
refresh fallback. HTTPS verification, no redirects/environment proxies,
response bounds, per-request timeouts and receive/deadline checks apply.

## Remaining activation work

This source provides the reader and the explicit CLI composition;
the scheduled workflow still uses legacy mode. Activation needs its
reviewed first-party workload-identity wiring, exact trusted target configuration,
the deployed/accepted broker, App permissions, complete cost authorization and
an owner-reviewed workflow step where applicable. Do not paste an ID token
into settings or claim that this optional Python parameter completed a cutover.

Before retirement, prove the real scheduled job with inherited PAT variables
absent, actual installation identity/limit, renewal, safe failures, correct
report totals and natural post-retirement runs. Local HTTP tests and source
integration against a fixture broker are not production acceptance.
