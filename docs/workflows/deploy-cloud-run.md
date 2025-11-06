# Deploy to Cloud Run - Reusable Workflow

**Location**: `.github/workflows/deploy-cloud-run.yml`

## Overview

Production-ready reusable workflow for deploying containerized applications to Google Cloud Run with comprehensive security controls, error handling, and validation.

## Security Features

### ✅ Input Validation & Sanitization
- **Service name validation**: Alphanumeric with hyphens, max 63 characters
- **Region whitelisting**: Common GCP regions validated
- **Project ID validation**: Format and length checks
- **Numeric input validation**: Instance scaling, CPU, memory, timeout, concurrency
- **Environment variable sanitization**: Prevents shell injection attacks
- **Secrets format validation**: Ensures proper secret:version format

### ✅ Robust Error Handling
- **Fail-fast behavior**: `set -euo pipefail` in all bash steps
- **Docker build validation**: Dockerfile existence, build success verification
- **Docker push verification**: Image digest retrieval and validation
- **Deployment validation**: JSON output parsing, URL verification
- **Health checks**: Multi-endpoint verification with fallbacks
- **Detailed error messages**: Context-rich failure reporting

### ✅ Secure Logging Practices
- **No secrets in logs**: Environment variables and secrets are masked
- **Conditional URL exposure**: Protected services get IAP instructions instead of direct URLs
- **Digest masking**: Only partial digest shown in logs
- **Safe deployment summary**: No sensitive data in GitHub Actions summary
- **Authentication warnings**: Alerts for unauthenticated production deployments

## Architecture Compliance

- ✅ **WIF Authentication**: Uses Workload Identity Federation (no service account keys)
- ✅ **SHA256 Digests**: Deploys using image digests for immutability
- ✅ **Concurrency Control**: Prevents concurrent deployments to same service
- ✅ **Environment-based**: Automatic production/development environment detection
- ✅ **Minimal Permissions**: `contents: read`, `id-token: write`

## Usage

### Basic Deployment

```yaml
name: Deploy to Cloud Run

on:
  push:
    branches: [main]

jobs:
  deploy:
    uses: merglbot-core/github/.github/workflows/deploy-cloud-run.yml@v2
    with:
      service: my-api
      project_id: my-project-prd
      region: europe-west1
      artifact_repo: merglbot
      dockerfile: Dockerfile
    secrets:
      GCP_WIF_PROVIDER: ${{ secrets.GCP_WIF_PROVIDER }}
      GCP_WIF_SERVICE_ACCOUNT: ${{ secrets.GCP_WIF_SERVICE_ACCOUNT }}
```

### Advanced Configuration

```yaml
jobs:
  deploy:
    uses: merglbot-core/github/.github/workflows/deploy-cloud-run.yml@v2
    with:
      # Required
      service: btf-api
      project_id: mb-portal-prd

      # Registry & Build
      region: europe-west1
      gar_location: europe
      artifact_repo: merglbot
      dockerfile: Dockerfile

      # Scaling Configuration
      min_instances: '1'
      max_instances: '20'
      memory: 1Gi
      cpu: '2'
      timeout: '60'
      concurrency: '100'

      # Environment Configuration
      env_vars: |
        API_VERSION=v2,
        LOG_LEVEL=info,
        ENABLE_METRICS=true

      # Secrets (from Secret Manager)
      secrets: |
        DATABASE_URL=database-connection-string:latest
        API_KEY=external-api-key:1

      # Authentication (use false for production with IAP)
      allow_unauthenticated: false
    secrets:
      GCP_WIF_PROVIDER: ${{ secrets.GCP_WIF_PROVIDER }}
      GCP_WIF_SERVICE_ACCOUNT: ${{ secrets.GCP_WIF_SERVICE_ACCOUNT }}
```

## Inputs

### Required Inputs

| Input | Type | Description |
|-------|------|-------------|
| `service` | string | Cloud Run service name (lowercase, alphanumeric with hyphens, max 63 chars) |
| `project_id` | string | GCP project ID |

### Optional Inputs

| Input | Type | Default | Description |
|-------|------|---------|-------------|
| `region` | string | `europe-west1` | GCP region for deployment |
| `artifact_repo` | string | `merglbot` | Artifact Registry repository name |
| `gar_location` | string | `europe` | Artifact Registry location |
| `dockerfile` | string | `Dockerfile` | Path to Dockerfile |
| `env_vars` | string | `''` | Comma-separated `KEY=VALUE` pairs |
| `secrets` | string | `''` | Newline-separated `KEY=secret:version` |
| `allow_unauthenticated` | boolean | `false` | Allow public access (use with caution) |
| `min_instances` | string | `'0'` | Minimum instances (0-1000) |
| `max_instances` | string | `'10'` | Maximum instances (1-1000) |
| `memory` | string | `512Mi` | Memory allocation (e.g., `512Mi`, `1Gi`) |
| `cpu` | string | `'1'` | CPU allocation |
| `timeout` | string | `'300'` | Request timeout in seconds (1-3600) |
| `concurrency` | string | `'80'` | Max concurrent requests per instance (1-1000) |

### Required Secrets

| Secret | Description |
|--------|-------------|
| `GCP_WIF_PROVIDER` | GCP Workload Identity Federation provider (format: `projects/PROJECT_NUMBER/locations/global/workloadIdentityPools/POOL/providers/PROVIDER`) |
| `GCP_WIF_SERVICE_ACCOUNT` | Service account email for WIF (format: `sa-name@project-id.iam.gserviceaccount.com`) |

## Outputs

| Output | Description |
|--------|-------------|
| `url` | Cloud Run service URL |
| `image` | Full image reference with digest |
| `revision` | Cloud Run revision name |

## Environment Variables Format

### Simple Format
```yaml
env_vars: API_VERSION=v2,LOG_LEVEL=info
```

### Multi-line Format (for readability)
```yaml
env_vars: |
  API_VERSION=v2,
  LOG_LEVEL=info,
  MAX_CONNECTIONS=100
```

**⚠️ Security Rules:**
- Only use for **non-sensitive** configuration
- Use uppercase with underscores: `MY_VAR_NAME`
- No shell metacharacters: `` $ ` \ ``
- For sensitive data, use `secrets` parameter instead

## Secrets Format

Secrets are mounted from Google Secret Manager.

```yaml
secrets: |
  DATABASE_URL=database-connection-string:latest
  API_KEY=external-api-key:1
  REDIS_PASSWORD=redis-credentials:2
```

**Format**: `ENVIRONMENT_VARIABLE=secret-manager-name:version`

- `ENVIRONMENT_VARIABLE`: Name exposed in Cloud Run container
- `secret-manager-name`: Name in Secret Manager
- `version`: Version number or `latest`

## Authentication & IAP

### Production (Recommended)
```yaml
allow_unauthenticated: false
```
- Requires authentication for all requests
- Use with Identity-Aware Proxy (IAP) for web access
- Or use service account authentication for service-to-service calls

### Public Services
```yaml
allow_unauthenticated: true
```
- ⚠️ **Use with caution** - service is publicly accessible
- Consider rate limiting and API key requirements
- Workflow will warn if used with production projects

## Validation & Error Handling

### Input Validation

The workflow validates all inputs before deployment:

1. **Service Name**: `^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$`
2. **Region**: Whitelisted GCP regions
3. **Project ID**: Valid GCP project format
4. **Numeric Inputs**: Positive integers within allowed ranges
5. **Memory Format**: `[0-9]+[MG]i?` (e.g., `512Mi`, `1Gi`)
6. **Environment Variables**: No shell injection characters
7. **Secrets**: Proper `key=name:version` format

### Error Scenarios

The workflow handles these error scenarios gracefully:

- ❌ Dockerfile not found
- ❌ Docker build failure
- ❌ Docker push failure
- ❌ Image digest not retrievable
- ❌ Cloud Run deployment failure
- ❌ Service URL not found
- ⚠️ Health check endpoints not accessible

Each failure provides detailed context and troubleshooting steps.

## Health Checks

The workflow attempts to verify deployment health:

1. **Waits 10 seconds** for service stabilization
2. **Tries common health endpoints**:
   - `/health`
   - `/healthz`
   - `/_health`
   - Root path `/`
3. **Checks HTTP status codes** (200-499 considered accessible)

**Note**: Health checks only run for `allow_unauthenticated: true` services.

## Deployment Summary

The workflow creates a comprehensive deployment summary with:

- ✅ Service details (name, region, project)
- ✅ Image information (repo, tag, digest)
- ✅ Configuration (memory, CPU, scaling, timeout)
- ✅ Authentication status
- ✅ Service URL (with security considerations)
- ✅ Health check status
- ❌ Troubleshooting steps (on failure)
- 🔄 Rollback commands (on failure)

**Security**: Summaries never expose secrets or sensitive configuration.

## Rollback Procedure

If deployment fails or issues are detected post-deployment:

```bash
# Rollback to previous revision
gcloud run services update-traffic SERVICE_NAME \
  --to-revisions=PREVIOUS_REVISION=100 \
  --project=PROJECT_ID \
  --region=REGION
```

Or use the rollback script:

```bash
./scripts/release/rollback-cloud-run.sh SERVICE_NAME \
  --project=PROJECT_ID \
  --region=REGION
```

## Troubleshooting

### Deployment Fails at Validation Step

**Symptom**: Workflow fails at "Validate inputs" step

**Solutions**:
- Check service name format (lowercase, alphanumeric, hyphens only)
- Verify region is a valid GCP region
- Ensure numeric values (CPU, memory, instances) are within limits
- Check env_vars and secrets format

### Docker Build Fails

**Symptom**: "Docker build failed" error

**Solutions**:
- Verify Dockerfile path is correct
- Check Dockerfile syntax
- Ensure all required files are present in build context
- Review build logs for specific errors

### Docker Push Fails

**Symptom**: "Docker push failed" error

**Solutions**:
- Verify Artifact Registry permissions
- Check that Artifact Registry repository exists
- Ensure WIF service account has `roles/artifactregistry.writer`
- Verify network connectivity to Artifact Registry

### Cloud Run Deployment Fails

**Symptom**: "Cloud Run deployment failed" error

**Solutions**:
- Verify WIF service account has `roles/run.admin`
- Check Cloud Run API is enabled in project
- Ensure project has sufficient quota
- Verify all required secrets exist in Secret Manager
- Check service account has `roles/secretmanager.secretAccessor` for secrets

### Health Check Fails

**Symptom**: "Health check endpoints not accessible"

**Solutions**:
- ℹ️ This is a **warning**, not a failure
- Verify application exposes a health endpoint
- Check application logs in Cloud Run console
- For authenticated services, health checks are skipped (expected)
- Manually verify service health after deployment

## IAM Requirements

### WIF Service Account Permissions

The service account specified in `GCP_WIF_SERVICE_ACCOUNT` needs:

```
roles/run.admin                         # Deploy and manage Cloud Run services
roles/iam.serviceAccountUser            # Act as Cloud Run runtime SA
roles/artifactregistry.writer           # Push Docker images
roles/artifactregistry.reader           # Pull Docker images
roles/secretmanager.secretAccessor      # Access secrets (if using secrets parameter)
```

### Workload Identity Pool Configuration

See [WARP_GITHUB_ACTIONS_REUSABLE_WORKFLOWS.md](https://github.com/merglbot-public/docs/blob/main/WARP_GITHUB_ACTIONS_REUSABLE_WORKFLOWS.md) for WIF setup.

## Best Practices

### ✅ DO

- Pin workflow to a specific version tag: `@v2`
- Use `allow_unauthenticated: false` for production
- Configure IAP for authenticated web services
- Use Secret Manager for sensitive configuration
- Set appropriate scaling limits based on load
- Use environment-specific configurations
- Monitor Cloud Run metrics and logs post-deployment

### ❌ DON'T

- Don't use `@main` for production workflows (use version tags)
- Don't put secrets in `env_vars` (use `secrets` parameter)
- Don't use `allow_unauthenticated: true` for production without additional security
- Don't set `max_instances` too high (cost implications)
- Don't ignore health check warnings without investigation
- Don't skip WIF setup (never use service account keys)

## Security Considerations

### Defense in Depth

This workflow implements multiple security layers:

1. **Input Validation**: Prevents injection attacks and misconfiguration
2. **WIF Authentication**: No static credentials in workflow
3. **Minimal Permissions**: Least privilege for workflow execution
4. **Secure Logging**: No secrets exposed in logs or summaries
5. **Image Digests**: Immutable image references prevent tampering
6. **Environment Isolation**: Automatic production/development environment detection
7. **Deployment Locking**: Concurrency control prevents race conditions

### Compliance

- ✅ WARP Security Standards compliant
- ✅ OWASP Top 10 considerations
- ✅ CIS Google Cloud Platform Foundation Benchmark aligned
- ✅ Least privilege IAM configuration
- ✅ Audit trail via GitHub Actions logs

## Examples

### Frontend Application

```yaml
jobs:
  deploy-frontend:
    uses: merglbot-core/github/.github/workflows/deploy-cloud-run.yml@v2
    with:
      service: website-frontend
      project_id: merglbot-prd
      region: europe-west1
      memory: 512Mi
      cpu: '1'
      max_instances: '20'
      allow_unauthenticated: true  # Public website
      env_vars: |
        NODE_ENV=production,
        API_URL=https://api.merglbot.ai
    secrets:
      GCP_WIF_PROVIDER: ${{ secrets.GCP_WIF_PROVIDER }}
      GCP_WIF_SERVICE_ACCOUNT: ${{ secrets.GCP_WIF_SERVICE_ACCOUNT }}
```

### Backend API with Secrets

```yaml
jobs:
  deploy-api:
    uses: merglbot-core/github/.github/workflows/deploy-cloud-run.yml@v2
    with:
      service: btf-api
      project_id: mb-portal-prd
      region: europe-west1
      memory: 2Gi
      cpu: '2'
      min_instances: '2'  # Keep warm
      max_instances: '50'
      timeout: '60'
      allow_unauthenticated: false  # Protected by IAP
      env_vars: |
        ENVIRONMENT=production,
        LOG_LEVEL=info,
        ENABLE_TRACING=true
      secrets: |
        DATABASE_URL=postgres-connection-string:latest
        REDIS_URL=redis-connection-string:latest
        JWT_SECRET=api-jwt-secret:1
        EXTERNAL_API_KEY=external-service-key:latest
    secrets:
      GCP_WIF_PROVIDER: ${{ secrets.GCP_WIF_PROVIDER }}
      GCP_WIF_SERVICE_ACCOUNT: ${{ secrets.GCP_WIF_SERVICE_ACCOUNT }}
```

### Multi-Environment Deployment

```yaml
jobs:
  deploy-dev:
    if: github.ref == 'refs/heads/develop'
    uses: merglbot-core/github/.github/workflows/deploy-cloud-run.yml@v2
    with:
      service: my-service-dev
      project_id: my-project-dev
      region: europe-west1
      max_instances: '5'
      allow_unauthenticated: true
      env_vars: ENVIRONMENT=development
    secrets:
      GCP_WIF_PROVIDER: ${{ secrets.DEV_GCP_WIF_PROVIDER }}
      GCP_WIF_SERVICE_ACCOUNT: ${{ secrets.DEV_GCP_WIF_SA }}

  deploy-prod:
    if: github.ref == 'refs/heads/main'
    uses: merglbot-core/github/.github/workflows/deploy-cloud-run.yml@v2
    with:
      service: my-service
      project_id: my-project-prd
      region: europe-west1
      min_instances: '1'
      max_instances: '20'
      allow_unauthenticated: false
      env_vars: ENVIRONMENT=production
    secrets:
      GCP_WIF_PROVIDER: ${{ secrets.PROD_GCP_WIF_PROVIDER }}
      GCP_WIF_SERVICE_ACCOUNT: ${{ secrets.PROD_GCP_WIF_SA }}
```

## Related Documentation

- [WARP GitHub Actions Standards](https://github.com/merglbot-public/docs/blob/main/WARP_GITHUB_ACTIONS_REUSABLE_WORKFLOWS.md)
- [Cloud Run Documentation](https://cloud.google.com/run/docs)
- [Workload Identity Federation Setup](https://cloud.google.com/iam/docs/workload-identity-federation)
- [Secret Manager Best Practices](https://cloud.google.com/secret-manager/docs/best-practices)

## Changelog

### v2.0.0 (Current)
- ✅ Comprehensive input validation and sanitization
- ✅ Robust error handling for all deployment steps
- ✅ Secure logging with no sensitive data exposure
- ✅ Health check verification
- ✅ Detailed deployment summaries
- ✅ Authentication-aware URL handling
- ✅ Production environment detection
- ✅ Rollback guidance on failure

## Support

For issues or questions:
- GitHub Issues: [merglbot-core/github](https://github.com/merglbot-core/github/issues)
- Documentation: [WARP Standards](https://github.com/merglbot-public/docs)
- Security Issues: See [SECURITY.md](https://github.com/merglbot-public/docs/blob/main/SECURITY.md)
