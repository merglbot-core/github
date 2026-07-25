---
title: "ENT Dependabot GitHub App Setup"
summary: "Repository-local mirror for the GitHub App identity used by the weekly ENT Dependabot closeout lane."
owner: "platform"
status: "active"
---

# ENT Dependabot GitHub App Setup

Canonical setup authority lives in
`merglbot-public/docs/ENT_DEPENDABOT_GITHUB_APP_SETUP.md`. This file is a
repo-local implementation mirror for operators working inside
`merglbot-core/github`.
Secret naming and no-log policy authority lives in
`merglbot-public/docs/MERGLBOT_SECRETS_NAMING_AND_LOGGING.md`.

Authority history for this implementation:

- `merglbot-public/docs#650` created the canonical GitHub App setup SSOT.
- `merglbot-public/docs#651` and `merglbot-public/docs#652` clarified
  fail-closed all-repo auth guardrails.
- `merglbot-public/docs#653` codified the Actions secret names and no-log
  contract for the app private key and Slack webhook.

The weekly ENT Dependabot closeout lane needs cross-org GitHub API access across
the dynamic Merglbot ENT scope resolved from `ENT_ORG_ALLOWLIST.md` plus live
non-archived/non-fork repository metadata. Fine-grained PATs are not appropriate
because they are bound to a single resource owner. Use a GitHub App installed in
the in-scope organizations instead.

## App Identity

- Name: `Merglbot ENT Dependabot Closeout`
- Homepage URL: `https://github.com/merglbot-core/github`
- Webhooks: disabled
- Installation scope: the currently allowlisted Merglbot orgs only
- Excluded scope: `Merglevsky-cz`

## Repository Permissions

Required:

- `Actions`: read and write
- `Checks`: read-only
- `Commit statuses`: read-only
- `Contents`: read and write
- `Issues`: read and write
- `Metadata`: read-only
- `Pull requests`: read and write

`Actions: read and write` is required so the engine can trigger the target
repo's Merglbot PR Assistant workflow through `workflow_dispatch`; issue-comment
review triggers are not the ENT apply path. `Contents: read and write` is
required for exact-head merges and GitHub's PR `update-branch` API. Dry-run and
close-only paths do not need contents writes, but the app identity must be able
to perform an exact-head PR merge when all gates pass. Branch protection,
required checks, Merglbot current-head review, third-party review-bot advisory evidence, and
`--match-head-commit` remain mandatory.

Do not grant `Administration` for the first live apply. Branch protection or
ruleset alignment must remain a separate, explicitly approved expansion.

## In-Scope Organizations

Install the app into these owners, selecting only the active platform repos when
using selective installation:

- `merglbot-autodoplnky`
- `merglbot-cerano`
- `merglbot-core`
- `merglbot-denatura`
- `merglbot-extractors`
- `merglbot-hodinarstvibechyne`
- `merglbot-kiteboarding`
- `merglbot-milan-private`
- `merglbot-proteinaco`
- `merglbot-public`
- `merglbot-ruzovyslon`
- `merglbot-shared`

## Selective Installation Guidance

Prefer `Repository access = All repositories` for each allowed organization so
new non-archived/non-fork repos are covered automatically after the allowlist is
updated. If you choose `Only select repositories`, generate the selection from
the current `ENT_ORG_ALLOWLIST.md` plus live repo metadata; do not hand-maintain
a static repository list in this mirror.

The engine keeps `scripts/dependabot/ent_repository_scope.txt` as a repo-local
mirror for low-blast-radius local `single_repo` diagnostics. GitHub Actions
`single_repo` runs validate against canonical remote `ENT_ORG_ALLOWLIST.md` plus
live non-archived/non-fork repository metadata, not the branch-local mirror.
ENT-wide `repo_scope=all` and multi-owner cohort runs require GitHub App auth.

## GitHub Actions Secrets

Store the generated app credentials in `merglbot-core/github` repository
secrets:

- `ENT_DEPENDABOT_APP_ID`: the GitHub App ID.
- `ENT_DEPENDABOT_APP_PRIVATE_KEY`: the full generated private key content.
- `SLACK_DEPENDABOT_WEBHOOK_URL`: the Slack incoming webhook URL for telemetry.

Never commit or paste the private key or Slack webhook value into issues, PRs,
logs, docs, or chat.
Only secret names and boolean configured/not-configured states may appear in
workflow logs or run artifacts.

## Verification

After the app is installed and secrets are present, run:

1. `ENT Dependabot Autonomous Closeout` in `dry-run` mode for
   `merglbot-public/docs`.
2. `ENT Dependabot Weekly Closeout` in `dry-run` mode for `repo_scope=all`.
3. Confirm the receipt records `scope_validation_status=validated_live`, a
   non-zero `repo_count`, the expected `org_count`, and Slack reports `sent`.
4. Build an explicit approval packet before any `apply` run.

## Guardrails Mirror

Manual `workflow_dispatch` defaults to `repo_scope=single_repo` with
`single_repo=merglbot-public/docs`, so the default manual run is a safe dry-run
smoke. Selecting `repo_scope=all` in any mode requires installed GitHub App
secrets, and `apply` also requires an explicit approval packet. Installation
lookup treats `404` as `app not installed` and propagates `401`, `403`, invalid
JWT, malformed key, timeout, and rate-limit responses as auth/API blockers.
