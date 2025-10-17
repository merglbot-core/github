# GitHub - Shared Workflows & CI/CD Docs

**Purpose:** Central repository for reusable GitHub Actions workflows and CI/CD documentation  
**Organization:** merglbot-core  
**Visibility:** Public (shared across organization)  
**Status:** Active - Used by all Merglbot repositories

---

## 📦 Contents

### Reusable Workflows (`.github/workflows/`)

Shared GitHub Actions workflows that can be called from other repositories.

**Available Workflows:**
- (List maintained in workflow files)

**Usage Example:**
```yaml
# In your repo's .github/workflows/deploy.yml
jobs:
  deploy:
    uses: merglbot-core/github/.github/workflows/deploy-cloud-run.yml@main
    with:
      service-name: my-service
      region: europe-west1
    secrets: inherit
```

---

### Documentation (`docs/`)

**Release Management:**
- `docs/release-management/` - Release procedures and guidelines

**Platform Tools:**
- `platform/tools/cost-monitoring/` - Cost monitoring tools

---

### Bot Configurations (`bot-configs/`)

GitHub bot configurations and templates.

---

### Training Materials (`training/`)

Training documentation and onboarding materials.

---

## 📚 Documentation

### Comprehensive Guides

**README_WARP_IMPLEMENTATION.md** (264 lines) - WARP implementation guide

**Related WARP Docs:**
- `WARP_GITHUB_ACTIONS_REUSABLE_WORKFLOWS.md` (merglbot-public/docs) - 491 lines
- `WARP_GLOBAL_RULES.txt` § GitHub Actions - Security best practices
- `WARP_QUICK_REFERENCE.md` §1, §9 - GitHub Actions rules

---

## 🚀 Using Reusable Workflows

### Benefits

- ✅ **DRY** - Don't repeat deployment logic
- ✅ **Consistency** - Same patterns across repos
- ✅ **Security** - Centralized security best practices
- ✅ **Maintenance** - Update once, affects all

### Best Practices

1. **WIF Authentication** (preferred over SA keys)
2. **Secrets in env vars** (never in if: conditions)
3. **set -euo pipefail** in bash scripts
4. **Concurrency groups** for deployments
5. **SHA256 digests** for images (never tags)

**See:** WARP_GLOBAL_RULES.txt § GitHub Actions

---

## 🔐 Security

**Critical Rules:**

```yaml
# ❌ NEVER
if: ${{ secrets.API_KEY != '' }}

# ✅ ALWAYS  
- id: check-secret
  run: |
    if [[ -n "${{ secrets.API_KEY }}" ]]; then
      echo "has_secret=true" >> $GITHUB_OUTPUT
    fi

- if: steps.check-secret.outputs.has_secret == 'true'
  run: # use secret
```

**WIF Setup:**
```yaml
- uses: google-github-actions/auth@v2
  with:
    workload_identity_provider: ${{ vars.GCP_WIF_PROVIDER }}
    service_account: ${{ vars.GCP_WIF_SERVICE_ACCOUNT }}
```

---

## 📖 Reference Implementations

**Working Examples:**

1. **merglbot-admin/.github/workflows/deploy-admin.yml**
   - Complete Cloud Run deployment
   - WIF authentication
   - Build, push, deploy pattern
   - Deployment summary

2. **merglbot-public/website/.github/workflows/**
   - Website deployment workflows
   - Content update patterns

**Use these as templates for new workflows!**

---

## 🎯 Contributing

**Adding New Reusable Workflow:**

1. Create in `.github/workflows/{workflow-name}.yml`
2. Use `workflow_call` trigger
3. Document inputs and secrets
4. Add to this README
5. Test from calling repository
6. Create PR

**Workflow Standards:**
- Follow WARP_GITHUB_ACTIONS_REUSABLE_WORKFLOWS.md
- Include error handling
- Add deployment summary
- Document rollback procedure

---

## 📝 Dashboard

**GitHub Dashboard:** `dashboard/`

Visualization and management dashboard for GitHub workflows and CI/CD status.

---

## 📞 Support

**Questions about workflows?**
- Read: WARP_GITHUB_ACTIONS_REUSABLE_WORKFLOWS.md (comprehensive)
- Check: WARP_GLOBAL_RULES.txt § GitHub Actions
- Reference: merglbot-admin workflows (latest patterns)

**For workflow issues:**
- Check logs in GitHub Actions UI
- Verify WIF permissions
- Ensure secrets are set correctly

---

**Central hub for CI/CD across Merglbot organization!** 🔄
