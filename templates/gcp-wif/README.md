# GCP Workload Identity Federation (WIF) Setup

> **Purpose**: Keyless authentication from GitHub Actions to GCP using OIDC.

## Why WIF?

| Method | Security | Maintenance | Audit |
|--------|----------|-------------|-------|
| Service Account JSON Key | ❌ Risk of leak | Manual rotation | Limited |
| **Workload Identity Federation** | ✅ Keyless | Automatic | Full IAM logs |

## Setup Steps

### 1. Create Workload Identity Pool (run once per GCP project)

```bash
# Set variables
PROJECT_ID="your-gcp-project-id"
POOL_NAME="github"
PROVIDER_NAME="github"
GITHUB_ORG="merglbot-core"  # or other org

# Create pool
gcloud iam workload-identity-pools create $POOL_NAME \
  --project=$PROJECT_ID \
  --location="global" \
  --display-name="GitHub Actions Pool"

# Create provider
gcloud iam workload-identity-pools providers create-oidc $PROVIDER_NAME \
  --project=$PROJECT_ID \
  --location="global" \
  --workload-identity-pool=$POOL_NAME \
  --display-name="GitHub OIDC" \
  --attribute-mapping="google.subject=assertion.sub,attribute.actor=assertion.actor,attribute.repository=assertion.repository,attribute.repository_owner=assertion.repository_owner" \
  --issuer-uri="https://token.actions.githubusercontent.com"
```

### 2. Create Service Account and Bind to WIF

```bash
SA_NAME="github-actions"
SA_EMAIL="${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"

# Create service account
gcloud iam service-accounts create $SA_NAME \
  --project=$PROJECT_ID \
  --display-name="GitHub Actions SA"

# Grant SA necessary roles (adjust as needed)
gcloud projects add-iam-policy-binding $PROJECT_ID \
  --member="serviceAccount:${SA_EMAIL}" \
  --role="roles/run.developer"

gcloud projects add-iam-policy-binding $PROJECT_ID \
  --member="serviceAccount:${SA_EMAIL}" \
  --role="roles/artifactregistry.writer"

# Allow GitHub to impersonate SA
gcloud iam service-accounts add-iam-policy-binding $SA_EMAIL \
  --project=$PROJECT_ID \
  --role="roles/iam.workloadIdentityUser" \
  --member="principalSet://iam.googleapis.com/projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${POOL_NAME}/attribute.repository_owner/${GITHUB_ORG}"
```

### 3. Get WIF Provider Resource Name

```bash
# Get the full provider resource name
gcloud iam workload-identity-pools providers describe $PROVIDER_NAME \
  --project=$PROJECT_ID \
  --location="global" \
  --workload-identity-pool=$POOL_NAME \
  --format="value(name)"
```

Output format:
```
projects/123456789/locations/global/workloadIdentityPools/github/providers/github
```

### 4. Add to GitHub Org Secrets

Add these secrets at org level:

| Secret | Value |
|--------|-------|
| `WIF_PROVIDER` | `projects/123456789/locations/global/workloadIdentityPools/github/providers/github` |
| `WIF_SERVICE_ACCOUNT` | `github-actions@your-project.iam.gserviceaccount.com` |
| `GCP_PROJECT_ID` | `your-gcp-project-id` |

## Usage in Workflows

```yaml
jobs:
  deploy:
    runs-on: ubuntu-latest
    permissions:
      contents: read
      id-token: write  # Required for WIF
    
    steps:
      - uses: actions/checkout@v4
      
      - name: Authenticate to GCP
        uses: google-github-actions/auth@v2
        with:
          workload_identity_provider: ${{ secrets.WIF_PROVIDER }}
          service_account: ${{ secrets.WIF_SERVICE_ACCOUNT }}
          
      - name: Setup gcloud
        uses: google-github-actions/setup-gcloud@v2
        
      - name: Deploy to Cloud Run
        run: |
          gcloud run deploy my-service \
            --image gcr.io/${{ secrets.GCP_PROJECT_ID }}/my-image \
            --region europe-west1
```

## Reusable Workflow

Use the reusable workflow from `merglbot-core/github`:

```yaml
jobs:
  deploy:
    uses: merglbot-core/github/.github/workflows/reusable-deploy-cloud-run.yml@main
    with:
      service_name: my-service
      region: europe-west1
      image: my-image
    secrets:
      WIF_PROVIDER: ${{ secrets.WIF_PROVIDER }}
      WIF_SERVICE_ACCOUNT: ${{ secrets.WIF_SERVICE_ACCOUNT }}
      GCP_PROJECT_ID: ${{ secrets.GCP_PROJECT_ID }}
```

## Per-Repo Restrictions (Optional)

For fine-grained access, restrict WIF to specific repos:

```bash
# Instead of org-wide access, restrict to specific repo
gcloud iam service-accounts add-iam-policy-binding $SA_EMAIL \
  --project=$PROJECT_ID \
  --role="roles/iam.workloadIdentityUser" \
  --member="principalSet://iam.googleapis.com/projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${POOL_NAME}/attribute.repository/${GITHUB_ORG}/${REPO_NAME}"
```

## Troubleshooting

### Error: "Unable to acquire OIDC token"
- Check `permissions.id-token: write` is set
- Verify WIF_PROVIDER format is correct

### Error: "Permission denied"
- Check SA has required IAM roles
- Verify WIF binding matches repo/org

## References

- [google-github-actions/auth](https://github.com/google-github-actions/auth)
- [GCP Workload Identity Federation](https://cloud.google.com/iam/docs/workload-identity-federation)
- [MERGLBOT IAC Standards](../../../merglbot-public/docs/IAC_STANDARDS.md)
