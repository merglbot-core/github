# Setup GCP Authentication - Composite Action

Centralized GCP authentication using Workload Identity Federation (WIF/OIDC).

## Purpose

Eliminates duplicated GCP authentication steps across deployment workflows. Enforces WIF-only (no SA JSON keys) per MERGLBOT security policy.

## Usage

```yaml
jobs:
  deploy:
    runs-on: ubuntu-latest
    permissions:
      id-token: write  # Required for OIDC
      contents: read
    
    steps:
      - uses: actions/checkout@v4
      
      - name: Authenticate to GCP
        id: gcp-auth
        uses: merglbot-core/github/.github/actions/setup-gcp-auth@v2
        with:
          workload_identity_provider: ${{ secrets.GCP_WIF_PROVIDER }}
          service_account: ${{ secrets.GCP_WIF_SERVICE_ACCOUNT }}
          project_id: 'my-gcp-project'
      
      - name: Deploy to Cloud Run
        run: |
          gcloud run deploy my-service \
            --image gcr.io/${{ steps.gcp-auth.outputs.project_id }}/my-image \
            --region europe-west1
```

## Inputs

| Input | Required | Default | Description |
|-------|----------|---------|-------------|
| `workload_identity_provider` | ✅ Yes | - | GCP WIF provider |
| `service_account` | ✅ Yes | - | Service account email |
| `project_id` | ❌ No | `''` | GCP Project ID (optional, sets the default project) |
| `export_environment_variables` | ❌ No | `'true'` | Export `GCLOUD_PROJECT`, `GCP_PROJECT`, `GOOGLE_CLOUD_PROJECT` |

## Outputs

| Output | Description |
|--------|-------------|
| `project_id` | Effective project ID (explicit input or detected from the active gcloud config) |

## Requirements

- Workflow must have `id-token: write` permission
- WIF pool and provider must be configured in GCP
- Service account must have required IAM roles

## Security

✅ **OIDC/WIF only** - No long-lived service account JSON keys  
✅ **Minimal permissions** - Scoped to job level  
✅ **Credential cleanup** - Automatic cleanup after workflow  

## References

- [MERGLBOT_GITHUB_ACTIONS_GLOBAL_RULES.md](https://github.com/merglbot-public/docs/blob/main/MERGLBOT_GITHUB_ACTIONS_GLOBAL_RULES.md)
- [AUTHENTICATION_AUTHORIZATION.md](https://github.com/merglbot-public/docs/blob/main/AUTHENTICATION_AUTHORIZATION.md)
- [Google GitHub Actions Auth](https://github.com/google-github-actions/auth)

---

*Created: 2025-11-12 | Part of Enterprise CI Audit*
