# GCP Workload Identity Federation (WIF) Setup (SSOT)

> **Purpose**: Keyless authentication from GitHub Actions to GCP using OIDC and the **central, Terraform-managed WIF pool**.

## SSOT Summary (Merglbot)

**Do not create per-project pools.** SSOT is a single central pool+provider in `merglbot-seed`:

- **Seed project**: `merglbot-seed` (project number: `671585034644`)
- **Pool**: `projects/671585034644/locations/global/workloadIdentityPools/github-actions`
- **Provider**: `projects/671585034644/locations/global/workloadIdentityPools/github-actions/providers/github-oidc`
- **Terraform source**: `merglbot-core/infra` → `terraform/v2/bootstrap/wif.tf`

## Per-Repo Setup (What you create)

For each repo/workflow that needs GCP access:

1. Create a **dedicated deploy service account** in the **target** GCP project (active project, not legacy/DELETE_REQUESTED).
2. Add **WIF impersonation** binding restricted to that repo:
   - `roles/iam.workloadIdentityUser`
   - `principalSet://iam.googleapis.com/projects/671585034644/locations/global/workloadIdentityPools/github-actions/attribute.repository/<org>/<repo>`
3. Grant the service account least-privilege IAM roles (Cloud Run deploy, Artifact Registry push, etc.).

### Example (gcloud)

```bash
SEED_PROJECT_NUMBER="671585034644"
POOL="github-actions"
REPO="merglbot-proteinaco/viz-api"

TARGET_PROJECT_ID="merglbot-proteinaco-main"
SA_ID="github-vizapi-deploy"
SA_EMAIL="${SA_ID}@${TARGET_PROJECT_ID}.iam.gserviceaccount.com"

# Create deploy SA
gcloud iam service-accounts create "${SA_ID}" \
  --project="${TARGET_PROJECT_ID}" \
  --display-name="viz-api Deploy (GitHub Actions)"

# Allow GitHub repo to impersonate the SA (repo-restricted)
gcloud iam service-accounts add-iam-policy-binding "${SA_EMAIL}" \
  --project="${TARGET_PROJECT_ID}" \
  --role="roles/iam.workloadIdentityUser" \
  --member="principalSet://iam.googleapis.com/projects/${SEED_PROJECT_NUMBER}/locations/global/workloadIdentityPools/${POOL}/attribute.repository/${REPO}"
```

## GitHub Variables (Recommended)

Prefer org-level variables (and override repo-level only when necessary):

| Variable | Value |
|---|---|
| `GCP_WIF_PROVIDER` | `projects/671585034644/locations/global/workloadIdentityPools/github-actions/providers/github-oidc` |
| `GCP_WIF_SERVICE_ACCOUNT` | `<deploy-sa>@<target-project>.iam.gserviceaccount.com` |
| `GCP_PROJECT_ID` | `<target-project-id>` |

## Workflow Usage

```yaml
permissions:
  contents: read
  id-token: write

steps:
  - uses: actions/checkout@v4

  - name: Authenticate to GCP (WIF)
    uses: google-github-actions/auth@v2
    with:
      workload_identity_provider: ${{ vars.GCP_WIF_PROVIDER }}
      service_account: ${{ vars.GCP_WIF_SERVICE_ACCOUNT }}
```

## Troubleshooting

### `invalid_target` (auth)
Your `workload_identity_provider` points to a provider that is disabled/deleted (often a legacy project in `DELETE_REQUESTED`).

Fix: use the SSOT provider from `merglbot-seed` (above).

### `permission denied` (impersonation)
The deploy SA is missing the `roles/iam.workloadIdentityUser` binding to the seed pool principalSet for your repo.

## References

- `merglbot-public/docs/IAC_STANDARDS.md` (SSOT rules)
- `merglbot-public/docs/GCP_ARCHITECTURE_V2_CANONICAL.md` (SSOT architecture)
- https://github.com/google-github-actions/auth
