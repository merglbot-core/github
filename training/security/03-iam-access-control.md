# Module 1.3: IAM & Access Control (WIF/OIDC)

## 🎯 Objectives
- Understand Workload Identity Federation (WIF) and OIDC-based auth
- Configure GitHub Actions → GCP authentication without keys
- Apply least-privilege IAM for CI and runtime SAs

## 🔐 Key Concepts
- WIF Provider in seed project (mb-seed)
- GitHub OIDC → impersonate target project SA
- No long-lived JSON keys in CI (fallback only)

## ✅ Minimal Setup (GitHub Actions)
```yaml
permissions:
  contents: read
  id-token: write  # REQUIRED for OIDC

jobs:
  plan:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: google-github-actions/auth@v2
        with:
          workload_identity_provider: ${{ vars.GCP_WIF_PROVIDER }}
          service_account: ${{ vars.GCP_WIF_SERVICE_ACCOUNT }}
      - run: gcloud auth list
```

## 🧩 IAM Roles (Least-Privilege)
- CI SA (per project):
  - roles/storage.objectAdmin (tf-state bucket if needed)
  - roles/iam.serviceAccountUser (impersonation)
  - roles/run.admin (only if deploying Cloud Run)
- Runtime SA (per service):
  - roles/secretmanager.secretAccessor (if needed)
  - roles/logging.logWriter

## 🛡️ Guardrails
- Never grant roles/owner
- One WIF pool/provider (mb-seed)
- Labels everywhere: env, service, tenant, owner, costcenter

## 🔎 Verification
```bash
# Expect one gcloud active account and project set
gcloud auth list
gcloud config get project
```

## 📚 References
- GCP Docs: Workload Identity Federation
- GitHub Actions: OpenID Connect with Google Cloud
- Internal MERGLBOT rules: mb-seed, WIF, labels, least-privilege
