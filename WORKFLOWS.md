# Workflow Catalogue

This repository hosts all reusable and scheduled GitHub Actions workflows for the Merglbot platform.  
Every consumer **must** follow [Rulebook v2](https://github.com/merglbot-public/docs/blob/main/RULEBOOK_V2.md), [PR Policy](https://github.com/merglbot-public/docs/blob/main/PR_POLICY.md), [Branch Protection](https://github.com/merglbot-public/docs/blob/main/BRANCH_PROTECTION.md), and [MERGLBOT_GITHUB_ACTIONS_REUSABLE_WORKFLOWS.md](https://github.com/merglbot-public/docs/blob/main/MERGLBOT_GITHUB_ACTIONS_REUSABLE_WORKFLOWS.md).

| Workflow | Purpose | Trigger / How to use | Required Inputs & Notes | Required Secrets / Status |
| --- | --- | --- | --- | --- |
| `.github/workflows/merglbot-pr-assistant-v1-reusable.yml` | Claude-powered PR reviewer (suggest/apply modes). | `workflow_call` from repo-level wrapper. Typically triggered on `pull_request` + optional `workflow_dispatch` with labels. | Inputs: `trigger_labels`, `skip_labels`, `model`, `temperature`, `max_output_chars`, `max_diff_lines`, `mode`. Requires caller to guard against fork PRs. | Secret `ANTHROPIC_API_KEY` (inherit). Produces `ai-review` comment; status = `review`. |
| `.github/workflows/length-check.yml` | Ensures PR title ≤100 chars, body ≤4000 bytes. | `workflow_call`. Usually configured as org-level required workflow. | No inputs. Uses PR payload to compute lengths. | No secrets. Status name `PR Text Length Check (central)`. |
| `.github/workflows/reusable-codeql-analysis.yml` | Shared CodeQL scan for JS/TS repos. | `workflow_call` with `languages` input (default `javascript`). | Consumers should set job name `codeql` to satisfy required status `[build, test, codeql]`. | No secrets needed; uses default `actions` permissions. |
| `.github/workflows/reusable-deploy-cloud-run.yml` | Builds, pushes, scans (Trivy HIGH/CRITICAL), signs (keyless Cosign via GitHub OIDC), and deploys a container to Cloud Run via WIF. | `workflow_call` from service repos once CI checks pass. | Inputs: `service`, `region`, `project_id`, `service_account`, `environment`, `dockerfile`, `env_vars`, `secrets`. Emits artifact digest via job summary and a CycloneDX SBOM artifact (365-day retention; attached to private-tag releases by default). | Secrets: `GCP_WIF_PROVIDER`, `GCP_WIF_SERVICE_ACCOUNT`, `GAR_LOCATION`. Required permissions: `contents: write`, `id-token: write`, `security-events: write`. |
| `.github/workflows/automated-release.yml` | Semantic-release automation for repos on `main`. | Triggered on push to `main` (excluding docs) and manual `workflow_dispatch` with optional version. | Auto-determines version unless `inputs.version` supplied. Publishes changelog + release notes. | Requires default `GITHUB_TOKEN` write access; ensure repo secrets contain publishing credentials if needed. |
| `.github/workflows/cost-monitoring.yml` | Daily FinOps report + optional issue creation. | Scheduled daily cron + manual dispatch (`month`, `dry_run`). | Runs Python tooling to read billing exports and summarise costs. Set `inputs.dry_run=true` for validation. | Uses WIF (permissions `id-token: write`). Requires project-level secret manager access configured in the workflow. |
| `.github/workflows/forecast-d1-readiness.yml` | Forecast D‑1 readiness check for final tables (inventory-driven) with Slack PASS/FAIL notifications. | Scheduled hourly (09:15–16:15 Europe/Prague; DST-safe via UTC superset + script NOOP) + manual dispatch (`patch_date_local`, `dry_run`). | Required scope = all (all countries required). Artifacts: CSV/MD/JSON report + `forecast_d1_readiness_channels_report.csv`.<br>Slack channel checks: `proteinaco/*`, `denatura/*`, `autodoplnky/cz`, `cerano/*`, `livero/*` → `Google Ads` + `Facebook`; `ruzovyslon/*` → `Google ads pmax`. | Uses WIF (`vars.GCP_WIF_PROVIDER`, `vars.GCP_WIF_SERVICE_ACCOUNT`). Slack via `secrets.SLACK_WEBHOOK_URL` (optional; skips if missing). |
| `.github/workflows/markdown-danger-lint.yml` | Lints Markdown PRs to block dangerous git push guidance (force-pushing all branches) and formatting issues. | `pull_request` (opened/edited/synchronize). | No inputs; auto-detects changed `.md` files. | No secrets. Status name `lint`. |
| `.github/workflows/quarterly-security-audit.yml` | Quarterly security checklist with optional auto fix / issue creation. | Cron (Jan/Apr/Jul/Oct 15th 09:00 UTC) + manual dispatch (`full_scan`, `create_issues`, `auto_fix`). | Runs shell/Python scripts defined in repo to audit org repos. Document findings in issues automatically. | Uses WIF to reach GCP / GitHub APIs (`id-token: write`). |

### Required Status Checks (`[build, test, codeql]`)

- Consumer repositories must expose three jobs named `build`, `test`, `codeql`.  
- `codeql` jobs should call `reusable-codeql-analysis.yml`.  
- `build`/`test` jobs typically run project-specific scripts (e.g., `npm run build`, `npm run lint`).

### Adding a New Workflow
1. Open an issue describing purpose + owners.
2. Follow MERGLBOT reusable workflow guidelines (least-privilege permissions, WIF, concurrency).
3. Document the workflow in this table.
4. Update consuming repos + branch protection rules if new statuses are required.
