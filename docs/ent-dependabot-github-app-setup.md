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
the canonical 42-repo Merglbot ENT scope. Fine-grained PATs are not appropriate
because they are bound to a single resource owner. Use a GitHub App installed in
the in-scope organizations instead.

## App Identity

- Name: `Merglbot ENT Dependabot Closeout`
- Homepage URL: `https://github.com/merglbot-core/github`
- Webhooks: disabled
- Installation scope: the 11 in-scope Merglbot orgs only
- Excluded scope: `Merglevsky-cz`

## Repository Permissions

Required:

- `Actions`: read-only
- `Checks`: read-only
- `Commit statuses`: read-only
- `Contents`: read-only
- `Issues`: read and write
- `Metadata`: read-only
- `Pull requests`: read and write

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

## Selective Installation Repository List

If you choose `Only select repositories`, install exactly these active repos:

- `merglbot-autodoplnky`: `autodoplnky-web`
- `merglbot-cerano`: `product-forecasting`, `feed-generator`, `cerano-web`, `viz-api`
- `merglbot-core`: `infra`, `tf-modules-`, `github`, `ai_prompts`, `merglbot-admin`, `platform`, `dataform`, `fb-viz-api`, `agents-orchestrator`, `project-management-app`
- `merglbot-denatura`: `marketing_actions_detector`, `marketing-planning`, `denatura-fb-viz`
- `merglbot-extractors`: `denatura-additional-costs-extractor`, `denatura-shoptet-export-extractor`, `facebook-extractor`, `ruzovyslon-forecast-exporter`, `shoptet-extractor`
- `merglbot-hodinarstvibechyne`: `hodinarstvi-web`
- `merglbot-kiteboarding`: `kiteboarding-web`
- `merglbot-milan-private`: `fakturoid`, `plane_so`
- `merglbot-proteinaco`: `proteinaco-web`, `btf-viz`, `viz-api`, `basket-analysis`, `abc_product_material_analysis`, `btf-legacy-implementation`
- `merglbot-public`: `docs`, `website`, `kazdavterina-web`, `livero-web`
- `merglbot-ruzovyslon`: `kbc_data_quality_metodology`, `business_forecasting`, `data-pipelines`, `ruzovyslon-web`, `viz-api`

The engine keeps `scripts/dependabot/ent_repository_scope.txt` as a repo-local
mirror for low-blast-radius local `single_repo` diagnostics. GitHub Actions
`single_repo` runs validate against canonical remote `REPOSITORY_MAP.md` on
`main`, not the branch-local mirror. ENT-wide `repo_scope=all` and multi-owner
cohort runs require GitHub App auth.

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
3. Confirm the receipt scans 42 repos and Slack reports `sent`.
4. Build an explicit approval packet before any `apply` run.

## Guardrails Mirror

Manual `workflow_dispatch` defaults to `repo_scope=single_repo` with
`single_repo=merglbot-public/docs`, so the default manual run is a safe dry-run
smoke. Selecting `repo_scope=all` in any mode requires installed GitHub App
secrets, and `apply` also requires an explicit approval packet. Installation
lookup treats `404` as `app not installed` and propagates `401`, `403`, invalid
JWT, malformed key, timeout, and rate-limit responses as auth/API blockers.
