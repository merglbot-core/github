# Workflow Catalogue

## Staged deployment and native debounce (github#962)

The secrets-based `reusable-deploy-cloud-run.yml` provides two explicit caller
inputs; existing callers keep the old deployment mode until their reviewed pins
and inputs change. Source delivery alone does not activate either feature.

- `debounce_environment`: a dedicated, owner-configured environment with exactly
  one ten-minute wait timer. Keep the production/preview environment and its
  existing protections on the deploy job. The timer job has only read permissions
  and no cloud identity. GitHub performs the wait before allocating its runner;
  there is no paid ten-minute shell sleep. The admission checks the live timer,
  this workflow run's age, the triggering same-repository successful `ci` push
  run, its current conclusion, and current `main`. A superseded head skips the
  deploy; a missing timer, API error, or changed CI result refuses admission.
  Manual dispatch is deliberately not inferred to be green CI in this mode.
- `staged_health_path`: the reviewed HTTP startup endpoint, `/healthz` for the
  admin and PM targets. The workflow captures the existing resolved traffic and
  tags, stages a digest-pinned, named revision with `--no-traffic`, and preserves
  that split. All cross-project references are supplied through native SDK
  `--update-secrets` before startup; staged mode never patches service YAML
  after creating an incomplete candidate. It validates the final
  revision's service/project identity, observed generation, digest, successful
  HTTP startup probe and unchanged traffic/ingress before promoting that exact
  revision. The configured probe uses port 8080, period 10 seconds, timeout 5
  seconds and 24 attempts. The hub helper is embedded from the same source;
  tests reject drift between the embedded and standalone implementations.

Staging requires an existing service with resolved traffic and `RoutesReady=True`.
A failed latest-created candidate does not prevent recovery when the existing
routes remain ready; the new candidate still requires its own Ready and
ContainerHealthy proof. Tags are captured as resolved revision identities, even
when originally bound to LATEST, and must retain that identity after promotion.

The debounce contract assumes a workflow named `ci` and the branch `main`.
Debounce requests only `contents: read` and `actions: read`, a subset of the
existing deploy job permissions. Both deploy and release already required
`actions: read` before this feature (hub baseline `8ffa841`); enabling or disabling
debounce does not raise that caller permission floor. GitHub validates nested job
permissions before evaluating their guards, so callers must retain the complete
existing permission contract listed below even when debounce is disabled.
The timer read uses `GET /repos/{owner}/{repo}/environments/{environment_name}`.
GitHub's [Get an environment permission contract](https://docs.github.com/en/rest/deployments/environments#get-an-environment)
lists **Actions: read** for GitHub App installation tokens, including access to
private repositories. The job uses its repository-scoped `github.token`; it does
not create or update environments. **Administration: write** belongs to the
separate environment mutation endpoint and is not requested here. A failed GET
still refuses admission; production caller activation must verify this read with
the actual job token rather than infer access from an owner's CLI token.
Shared concurrency preserves a running deployment; GitHub may replace an older
pending deployment with a newer pending one. Only the current main head with a
fresh successful CI result may enter the deploy job after the native timer.

HTTP startup success proves the configured endpoint at startup, not full business
acceptance. Cloud Run accepts HTTP success/redirect responses for this probe;
caller review must establish that the chosen endpoint cannot redirect to a login
or unrelated healthy page. Production business smoke checks remain necessary.
The private admin ingress is not opened to test a tag URL. The deploy group is
scoped by project, region and service and never cancels an executing deployment.
External writers must respect that serialization; the pre-promotion readback is
not a Cloud Run cross-client transaction lock.

The summary records the exact pre-deploy revision split for rollback. On failure
before promotion the old split is retained; a failed post-promotion readback is an
incident requiring fresh traffic evidence before rollback, never a blind rewrite
of a possible concurrent owner's change. This feature does not delete revisions,
alter IAM, change the original acceptance windows, or prove the deploy-count and
30-minute freshness targets. Timer activation and workflow merges retain their
owner-only boundary. Consumer rollout and runtime acceptance remain under #962.

### Returning from staged to legacy deployment

Disabling `staged_health_path` alone is **not a rollback**. `--no-traffic` and
named-revision promotion leave traffic pinned to a revision. A later legacy
deployment may create a healthy revision without serving it; a successful deploy
command does not establish serving acceptance in this state.

Before disabling staged mode, prepare a separately owner-authorized transition:

1. Serialize against every deployment and external traffic writer. Read the live
   service and record its resolved traffic split, tags, latest-created and
   latest-ready revisions, and the currently serving revision image digests.
   Retain the exact named-revision split as the rollback target.
2. Establish that the latest-created revision equals the latest-ready revision
   and the intended healthy serving revision. Verify its immutable image digest,
   source binding, startup health and business smoke evidence. If an unserved,
   failed or foreign candidate is latest, stop: switching to LATEST could expose
   it. Resolve that candidate through a separately reviewed deployment first.
3. With fresh unchanged revision/traffic evidence, execute the approved Cloud Run
   traffic transition `gcloud run services update-traffic SERVICE --project PROJECT
   --region REGION --to-latest`. This is a production traffic mutation, never an
   automatic effect of disabling an input. Preserve existing tags and ingress.
4. Read the service back and require 100% LATEST traffic resolving to the verified
   intended revision, unchanged tags and ingress, and successful business smoke.
   Only then disable staged mode through the authorized caller change. Verify
   the next legacy deployment actually serves its intended digest; its CLI exit
   status alone is insufficient.
5. If verification fails, treat it as an incident. Re-read current traffic and
   writers before an authorized rollback to the recorded named-revision split;
   never overwrite a concurrent change blindly. Restoring a named split requires
   keeping staged mode until a new verified LATEST transition succeeds.

This repository hosts all reusable and scheduled GitHub Actions workflows for the Merglbot platform.  
Every consumer **must** follow [Rulebook v2](https://github.com/merglbot-public/docs/blob/main/RULEBOOK_V2.md), [PR Policy](https://github.com/merglbot-public/docs/blob/main/PR_POLICY.md), [Branch Protection](https://github.com/merglbot-public/docs/blob/main/BRANCH_PROTECTION.md), and [MERGLBOT_GITHUB_ACTIONS_REUSABLE_WORKFLOWS.md](https://github.com/merglbot-public/docs/blob/main/MERGLBOT_GITHUB_ACTIONS_REUSABLE_WORKFLOWS.md).

| Workflow | Purpose | Trigger / How to use | Required Inputs & Notes | Required Secrets / Status |
| --- | --- | --- | --- | --- |
| `.github/workflows/merglbot-pr-assistant-v1-reusable.yml` | Claude-powered PR reviewer (suggest/apply modes). | `workflow_call` from repo-level wrapper. Typically triggered on `pull_request` + optional `workflow_dispatch` with labels. | Inputs: `trigger_labels`, `skip_labels`, `model`, `temperature`, `max_output_chars`, `max_diff_lines`, `mode`. Requires caller to guard against fork PRs. | Secret `ANTHROPIC_API_KEY` (inherit). Produces `ai-review` comment; status = `review`. |
| `.github/workflows/length-check.yml` | Ensures PR title ≤100 chars, body ≤4000 bytes. | `workflow_call`. Usually configured as org-level required workflow. | No inputs. Uses PR payload to compute lengths. | No secrets. Status name `PR Text Length Check (central)`. |
| `.github/workflows/reusable-codeql-analysis.yml` | Shared CodeQL scan for JS/TS repos; also `'["actions"]'` for weekly workflow-file analysis when the caller runs it on `schedule` (github#961). | `workflow_call` with `languages` input (default `javascript`) and optional `paths-ignore` (JSON array of globs, default `[]` = no exclusion; passed to `codeql-action/init` as an inline config, interpreted languages only); the caller owns the trigger (`schedule`/`push`/`pull_request`) and must grant `security-events: write`, `actions: read`, `contents: read`. Upload is rejected while CodeQL default setup is enabled on the same repo, so disable default setup in the same change. | Consumers should set job name `codeql` to satisfy required status `[build, test, codeql]`. | No secrets needed; uses default `actions` permissions. |
| `.github/workflows/ci-python-delay-candidate.yml` | Dedicated opt-in Python CI for the bounded ten-minute PR test-delay pilot; the established `ci-python.yml` remains unchanged. | `workflow_call`, pinned to an exact commit by a pilot caller. Only same-repo PRs to `main` on their first attempt use `ci-pr-delay` when the caller passes `ci-delay-candidate: true` and sets `CI_DELAY_ENABLED=true`; all other runs use `ci-immediate`. | Requires clean caller environments: `ci-pr-delay` admits PR merge refs, while `ci-immediate` admits every caller ref routed there, including `main` and PR merge refs. The timer is configured only on `ci-pr-delay`. Preserves Python tests and the `Python CI` job name, records checkout head/base and the selected environment in one aggregate acceptance marker, and uses `deployment: false`. | No secrets or cloud login. `contents: read`; the caller's existing required check remains mandatory. |
| `.github/workflows/reusable-build-attest.yml` | Builds a local linux/amd64 image, reclaims the selected builder's local BuildKit cache while retaining the tagged scan image, runs fail-closed Trivy before any registry write, then publishes an isolated candidate with SBOM/provenance and promotes release tags only after local/published config-digest parity. | `workflow_call`; callers must pin an exact commit SHA. `scan=false` remains an explicit opt-out, while the default is fail-closed scanning. | Candidate tags are unique to the workflow run. A failed scan creates no Artifact Registry version; a parity failure leaves only the quarantined candidate and never moves branch/SHA/latest tags. | WIF resource names are passed through `wif_provider` and `wif_service_account`; Binary Authorization signing runs only when both `attest` and `push` are enabled. |
| `.github/workflows/reusable-deploy-cloud-run.yml` | Builds, pushes, scans (Trivy HIGH/CRITICAL), signs (keyless Cosign via GitHub OIDC), and deploys a container to Cloud Run via WIF. | `workflow_call` from service repos once CI checks pass. | Inputs: `service`, `region`, `project_id`, `service_account`, `environment`, `dockerfile`, `env_vars`, `secrets`. For cross-project secrets the workflow uses recovery-safe ordering: base rollout first, then fresh YAML export + alias reconcile, never stale YAML replay before the target image/env rollout. Publishes a minimal deployment job summary and only caller-visible outputs produced by workflow-owned steps: `deployment_mode`, plus optional alias-only coordination outputs such as alias names or counts when a same-org caller needs them. All other coordination stays internal to the reusable workflow, and full Secret Manager resource paths must never leave the workflow via outputs or summaries. Also uploads a CycloneDX SBOM artifact (365-day retention; attached to private-tag releases by default). Supports private GitHub Packages during Docker build only through the built-in BuildKit secret mount pattern (`id=node_auth_token`) backed by `github.token`; this is not a generic build-arg/build-secret interface. | Secrets: `GCP_WIF_PROVIDER`, `GCP_WIF_SERVICE_ACCOUNT`, `GAR_LOCATION`. Current caller permissions remain `contents: write`, `id-token: write`, `security-events: write`, `actions: read` because GitHub validates nested job permissions before the tag-only `publish_release_sbom` guard is evaluated; add `packages: read` when the Docker build resolves private GitHub Packages through the built-in secret-mount path. A future split into separate read-only deploy and release-SBOM entrypoints can lower normal deploy callers back to `contents: read`. |
| `.github/workflows/reusable-deploy-cloud-run-wif.yml` | Builds (or adopts), scans (Trivy) and deploys a container to Cloud Run by digest via WIF. | `workflow_call`, pinned by merged commit SHA. | Image publication here and in `reusable-deploy-cloud-run.yml` (github#909): the `:<github.sha>` tag is probed with `docker pull`. A present tag is adopted (no rebuild, no push) only when its digest passes the workflow's `adoptable` check, because the registry repository can be shared with other repos' deploy identities: `reusable-deploy-cloud-run.yml` requires a `cosign verify` of that digest with its own keyless identity (that hub workflow file, GitHub OIDC issuer, same caller repository, and a signing run of this very commit via `--certificate-github-workflow-sha`; cosign is installed before the build); this WIF workflow does not sign, so it never adopts. A pulled tag whose digest cannot be resolved fails. A present tag without that evidence and an absent tag (`manifest unknown` / `not found`) are built + pushed (an immutable-tag repo refuses the re-push; a concurrently published tag is adopted only with the same evidence); any other probe error fails the job. Both paths resolve a `sha256` digest without pipes and scan + deploy by digest. The build step exposes `adopted`; the signing workflow re-verifies an adopted image before (not) re-signing it. | Secrets `WIF_PROVIDER`, `WIF_SERVICE_ACCOUNT`, `GCP_PROJECT_ID` (or the matching inputs). |
| `.github/workflows/automated-release.yml` | Semantic-release automation for repos on `main`. | Triggered on push to `main` (excluding docs) and manual `workflow_dispatch` with optional version. | Auto-determines version unless `inputs.version` supplied. Publishes changelog + release notes and emits explicit `release_outcome` values: `release_created`, `release_already_exists`, `release_skipped_no_version`. | Requires default `GITHUB_TOKEN` write access. The Slack `notify` job was removed (owner decision 16 Oct 2026, merglbot-core/infra#3841); the outcome lives in the step summary and the GitHub release. |
| `.github/workflows/cost-monitoring.yml` | Daily FinOps report + optional issue creation. | Scheduled daily cron + manual dispatch (`month`, `dry_run`). | Runs Python tooling to read billing exports and summarise costs. Set `inputs.dry_run=true` for validation. | Uses WIF (permissions `id-token: write`). Requires project-level secret manager access configured in the workflow. |
| `.github/workflows/forecast-d1-readiness.yml` | Manual-only Forecast D‑1 readiness diagnostic for final tables (inventory-driven). The scheduled lane was retired after the recurring report moved to the Codex thread. | Manual dispatch only (`patch_date_local`, `dry_run`, `send_success_rate`); `dry_run=true` by default. Scheduled D‑1/D‑2 checks, the dedicated real-readiness lanes, the daily 30d success-rate message, and scheduled artifact production are retired. | Manual runs retain the main D‑1/D‑2 and per-channel diagnostic artifacts and write the verdict to the step summary. `send_success_rate=true` enables the 30d diagnostic (step summary + artifact). With `dry_run=false` only a current-period result (D-1 = yesterday or today, Europe/Prague) touches the issue: NOT-READY opens/updates one `[final-data] D1 readiness: <scope>` issue (label `final-data-readiness`) and READY with a reported date closes it; a historical result (PASS or FAIL for an older `patch_date_local`) goes to the step summary only and never creates, edits or closes the issue (an open one gets at most a `[historical]` comment); each new issue is attached as a native sub-issue of the EPIC in `READINESS_PARENT_EPIC` (`#769`, attachment failure fails the step with the issue URL); a failed `gh issue list` fails the step. Slack alerts and the synthetic Slack preview input were removed (owner decision 16 Oct 2026, merglbot-core/infra#3841). | Uses WIF (`vars.GCP_WIF_PROVIDER`, `vars.GCP_WIF_SERVICE_ACCOUNT`) and `github.token` (`issues: write`). No Slack secret is required. |
| `.github/workflows/markdown-danger-lint.yml` | Lints Markdown PRs to block dangerous git push guidance (force-pushing all branches) and formatting issues. | `pull_request` (opened/edited/synchronize). | No inputs; auto-detects changed `.md` files. | No secrets. Status name `lint`. |
| `.github/workflows/quarterly-security-audit.yml` | Quarterly security checklist with optional auto fix / issue creation. | Cron (Jan/Apr/Jul/Oct 15th 09:00 UTC) + manual dispatch (`full_scan`, `create_issues`, `auto_fix`). | Runs shell/Python scripts defined in repo to audit org repos. Document findings in issues automatically. | Uses WIF to reach GCP / GitHub APIs (`id-token: write`). |
| `.github/workflows/reusable-deploy-runtime-changes.yml` | Deploy runtime-changes guard: decides whether a Cloud Run deploy run ships runtime changes by diffing the last successfully deployed commit against `deploy-sha` (github#909). | `workflow_call` as the first job of a deploy workflow; see the section below. | Inputs: `deploy-sha` (required), `workflow-file`, `deploy-job-regex`, `deny-extra`, `force-runtime-extra`, `runs-on`; outputs `runtime`, `reason`, `base-sha`. Every uncertainty is `runtime=true`. | No secrets. Caller job grants `actions: read` + `contents: read`. |

### Required Status Checks (`[build, test, codeql]`)

- Consumer repositories must expose three jobs named `build`, `test`, `codeql`.  
- `codeql` jobs should call `reusable-codeql-analysis.yml`.  
- `build`/`test` jobs typically run project-specific scripts (e.g., `npm run build`, `npm run lint`).

### Adding a New Workflow
1. Open an issue describing purpose + owners.
2. Follow MERGLBOT reusable workflow guidelines (least-privilege permissions, WIF, concurrency).
3. Document the workflow in this table.
4. Update consuming repos + branch protection rules if new statuses are required.

## pr-gate.yml

ONE job that runs the three cheap per-PR gates that used to be three separate jobs:
dependency review, PR hygiene checks and docs governance. Steps are copied verbatim from
`security-dependency-review.yml`, `utility-pr-checks.yml` and `reusable-docs-governance.yml` —
no rule, threshold, action pin or message changed. GitHub bills `ceil(seconds/60)` **per job**,
so three 7-12 s jobs cost 3 minutes while one ~30 s job costs 1 (github#877).

The merge-commit check stays advisory in both files: a merge commit is a warning. Its REST read is
retried three times and paginated, and if it still fails the step warns instead of failing, so a
GitHub API incident no longer blocks every PR (github#767).

Gitleaks used to stay separate because the job took 53-58 s. Measured again 22. 9. 2026 on
merglbot-extractors/shoptet-extractor, **45 of those seconds were `Upload SARIF report`** and the
scan itself was ~1 s, so `gitleaks: true` now folds the scan into this job as an opt-in step
(github#895). The step keeps the scan and the redacted `$GITHUB_STEP_SUMMARY` report and drops the
SARIF upload, so no `security-events: write` is inherited and PR Gate stays around 20 s. Repos that
switch it on delete their `security-gitleaks.yml` and keep a weekly full-tree scan for pushes that
never open a PR. GHAS Secret Protection push protection is untouched either way.

The standalone `security-gitleaks.yml` reusable is still the only path that persists findings to
code scanning (SARIF). Its job grants `actions: read` because `upload-sarif` reads the workflow run
and an enumerative job `permissions:` block drops any grant the caller adds (github#805: uploads had
silently failed since 4. 4. 2026 behind `continue-on-error`). The upload step now fails the check,
and the PR comment fires only when the scan itself fails. Measured 20. 10. 2026: two callers left
(`merglbot-public/docs`, `merglbot-denatura/acquisition-analysis`), both already grant `actions: read`.

### Optional steps (all default to `false`, so existing callers are unaffected)

| input | default | what it adds |
|---|---|---|
| `gitleaks` | `false` | gitleaks 8.18.4 (pinned by release checksum) over the working tree, `--no-git --redact`; findings fail the gate and are listed redacted in the job summary |
| `gitleaks-config-path` | `''` | repo-relative gitleaks config; absolute paths and `..` are rejected |
| `gitleaks-upload-report` | `false` | uploads the redacted JSON report as a 7-day artifact |
| `markdown-danger-lint` | `false` | fails when changed Markdown recommends force-pushing all branches at once (the pattern lives in `markdown-danger-lint.yml`; lines saying never or do not are allowed) |
| `pr-text-length` | `false` | PR title/body length limits, dependabot waived (verbatim from `length-check.yml`) |
| `pr-text-max-title` | `100` | title limit in characters |
| `pr-text-max-body` | `4000` | body limit in bytes |
| `runs-on` | `ubuntu-slim` | `ubuntu-slim` (1 vCPU, 0.002 USD/min, 15-min job cap) or `ubuntu-24.04`; any other value falls back to `ubuntu-24.04` (github#913) |
| `workflow-lint` | `false` | actionlint 1.7.12 + zizmor 1.30.1 (both pinned by release checksum, zizmor `--offline`) over `.github/workflows`; findings fail the gate and go to the log + job summary. No SARIF upload (it would need `security-events: write` on the job). Replaces the CodeQL default-setup `actions` language on repos with no other CodeQL language (github#961) |
| `zizmor-min-severity` | `medium` | zizmor `--min-severity` threshold (`unknown`, `informational`, `low`, `medium`, `high`) |
| `run-terraform-fmt` | `false` | `terraform fmt -check -recursive -diff` in `terraform-directory` (hashicorp/setup-terraform v4.0.1 pinned by SHA, wrapper off). `terraform validate` is deliberately not offered: `init` executes PR-chosen provider plugins and this job holds `pull-requests: write`; keep validate in a read-only caller job |
| `terraform-directory` | `.` | repo-relative directory for the terraform fmt step; absolute paths and `..` are rejected |
| `terraform-version` | `latest` | passed to setup-terraform; callers should pin an exact version |

`markdown-danger-lint` and `pr-text-length` read `github.event.pull_request.*`, so a caller that
switches them on needs `types: [opened, edited, synchronize, reopened]` — otherwise a title or body
edit does not re-run the check.

Two deliberate differences from the originals: the job runs with `pull-requests: write`
(dependency-review posts its summary comment; no pull-request-controlled code executes here), and
every functional step carries `if: ${{ !cancelled() }}` with a final gate step, so one failing part
no longer hides the other two the way three separate jobs never did.

Check context for branch protection = `<caller job NAME> / PR Gate`. GitHub composes it from the
caller job's **display name** (`name:`), falling back to the job id only when no `name:` is set.
Every current consumer (infra, agents-orchestrator, merglbot-admin, business-analytics) names the
job `PR Gate`, so the required context is **`PR Gate / PR Gate`** (`app_id 15368`); an unnamed
`pr-gate:` job would emit `pr-gate / PR Gate` instead. Read the context from a live check run on
the PR head before editing protection — never from this catalog (github#879, measured 21. 9. 2026).

```yaml
jobs:
  pr-gate:
    name: PR Gate
    uses: merglbot-core/github/.github/workflows/pr-gate.yml@<pinned-sha>
    permissions:
      contents: read
      pull-requests: write
    with:
      pull-request-number: ${{ github.event.pull_request.number }}
      fail-on-severity: moderate
      comment-summary-in-pr: true
      mode: advisory
```

## reusable-docs-governance.yml

SSOT-aware documentation obligation gate (docs-governance program, 2026-07). Deterministic
classifier (port of the agents-orchestrator `documentationObligation` semantics): an impactful
code PR must carry docs evidence — same-PR markdown, a `MERGLBOT_DOCS_SYNC: merglbot-public/docs#<pr>`
reference, or the `docs-impact: none` label + `DOCS_IMPACT_NONE_REASON:` line. Repo→SSOT-doc
mapping comes from `config/ssot-map.snapshot.json` (snapshot of `merglbot-public/docs/ssot-map.yaml`).
`mode: advisory` (default) never fails; `mode: enforce` fails only on `missing`; `unknown` is
fail-open in both. Check name for branch protection: **`docs-governance`**.

```yaml
jobs:
  docs-governance:
    uses: merglbot-core/github/.github/workflows/reusable-docs-governance.yml@<pinned-sha>
    with:
      mode: advisory
```

## reusable-deploy-runtime-changes.yml

Deploy runtime-changes guard (github#909). It replaces the inline `runtime-changes` job of the Cloud
Run deploy callers, whose per-repo allowlists drifted from the Dockerfile `COPY` sets, whose
`echo "$CHANGED" | grep -q` could SIGPIPE into a silent skip, and whose `HEAD^` base missed the
commits of replaced pending runs and multi-commit pushes. The job diffs the **last successfully
deployed commit** of the calling workflow against `deploy-sha` and skips only when every changed
path is deny-listed. The logic is `scripts/deploy-guard/runtime_changes.sh`, sparse-checked-out
from the hub commit the caller pinned (`job.workflow_sha`, as `pr-gate.yml` does for
docs-governance).

| `reason` | `runtime` | when (first match wins) |
|---|---|---|
| `manual`, `rerun` | true | `workflow_dispatch`, or `run_attempt > 1` |
| `fail-open:no-base` | true | none of the 10 newest completed `main` runs (current run excluded) has every job matching `deploy-job-regex` concluded `success`; or any API/jq error |
| `fail-open:marker-mismatch` | true | that run is a `push`/`workflow_dispatch` run (base = its `head_sha`) whose title carries a ` @ <40 hex>` marker that differs from `head_sha` |
| `fail-open:legacy-marker` | true | that run has another event (`workflow_run`), where the base is the run-name marker, and has no marker |
| `already-deployed` | false | base == `deploy-sha` |
| `fail-open:base-missing` | true | base commit is not in the clone |
| `stale-trigger` | false | `deploy-sha` is an ancestor of base (production is never rolled back) |
| `fail-open:diverged`, `fail-open:empty-diff` | true | base is not an ancestor of `deploy-sha`; or the diff is empty |
| `runtime:<path>` | true | first path that is force-runtime or not deny-listed |
| `non-runtime-only` | false | every path is deny-listed; a `::notice::` NO-DEPLOY line is emitted |

Deny-list (anchored bash ERE; add more with `deny-extra`, one per line): `^\.github/`, `^docs/`,
`^[^/]+\.md$`, `^(tests?|e2e|__tests__|playwright|cypress)/`,
`^\.(gitignore|gitattributes|editorconfig|pre-commit-config\.yaml)$`,
`^(LICENSE|LICENCE|COPYING|NOTICE)(\.(md|txt|rst))?$`, `^CODEOWNERS$`,
`^\.(vscode|devcontainer|claude|codex|cursor)/`. Force-runtime (add more with
`force-runtime-extra`): the calling workflow file and `^\.github/actions/`. Malformed inputs, an
invalid regex or any unexpected error fail open (`fail-open:*`). `dry-run`/`base-override` exist
only for `deploy-guard-selftest.yml`.

Caller contract: a `run-name` ending in ` @ <deployed sha>`, job permissions `actions: read` +
`contents: read`, and downstream jobs gated on `!cancelled() && … runtime != 'false'`, so a guard
job that fails for infrastructure reasons still deploys. `deploy-job-regex` must match the API
names of the deploying jobs (`deploy`, `deploy (<matrix>)`, `deploy / build-and-deploy`).

```yaml
run-name: Deploy my-service @ ${{ github.event.workflow_run.head_sha || github.sha }}
jobs:
  runtime-changes:
    uses: merglbot-core/github/.github/workflows/reusable-deploy-runtime-changes.yml@<pinned-sha>
    permissions:
      actions: read
      contents: read
    with:
      # The same SHA the run-name records and the deploy uses (workflow_run: the triggering head).
      deploy-sha: ${{ github.event.workflow_run.head_sha || github.sha }}
  deploy:
    needs: [runtime-changes]
    if: ${{ !cancelled() && needs.runtime-changes.outputs.runtime != 'false' }}
```

## reusable-deploy-cloud-run-wif.yml: env_vars

`env_vars` takes the same comma-separated `NAME=value` list as the secrets-based variant. It is set together
with `ENVIRONMENT` and **replaces** the service env, so pass the complete list. Plain values only: secret
mounts are not part of this input (github#709). Callers that need Secret Manager mounts stay on the
secrets-based variant until a reviewed `secrets` input exists.
## reusable-frontend-artifact-gate.yml

A `uses:` job gets a fresh checkout of the sources, so it can only gate a build that an earlier job
uploaded. Pass that artifact as `artifact_name`, and upload it with `include-hidden-files: true` so
`.well-known/` survives. Without it the job exits 2 ("cannot conclude"). Callers that build in a single
job run the gate CLI as a step instead (github#762). `frontend-artifact-gate-tests.yml` exercises
the upload, download and gate path on every change.

## ci-python.yml: docker-image-scan

`docker-image-scan: true` adds an `Image scan` job: in pull requests that change a `Dockerfile` or a
`requirements*.txt|.in` file it builds the image locally (never pushed) and scans it with Trivy
(HIGH/CRITICAL, ignore-unfixed). A filesystem scan cannot see the base image OS packages (github#775).
The required `Python CI` job is unchanged; an unreadable base commit resolves to scanning.

Staged deployment preserves service-wide authentication: it reads the existing
IAM policy and invoker-IAM-check setting before deploying, requires anonymous
invocation to match `allow_unauthenticated`, and omits both authentication mutation
flags. Access mismatches or ambiguous public grants require a separate authorized
IAM change; staging does not perform it. The caller needs permission to read the
service IAM policy; a failed read stops before deployment.
