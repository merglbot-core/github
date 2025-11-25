# GitHub Actions Policy as Code

## Overview

This directory contains OPA (Open Policy Agent) policies for validating GitHub Actions workflows. Policies are automatically enforced via Conftest on every PR that modifies workflows.

## Policies

### Security Policies

1. **SHA Pinning**: All actions must be pinned to commit SHA (not tags)
   - ✅ `uses: actions/checkout@b4ffde65f46336ab88eb53be808477a3936bae11`
   - ❌ `uses: actions/checkout@v4`

2. **No Hardcoded Secrets**: Secrets must use `${{secrets.*}}`
   - ✅ `PASSWORD: ${{ secrets.DB_PASSWORD }}`
   - ❌ `PASSWORD: "my-secret-123"`

3. **Explicit Permissions**: Workflows must have explicit `permissions:` blocks
   - ✅ `permissions: { contents: read }`
   - ❌ No permissions block (defaults to permissive)

4. **Pull Request Target Guards**: `pull_request_target` must have `if:` conditions
   - Prevents code injection from untrusted forks

### Compliance Policies (SOC2)

1. **Production Approvals**: Production deploys should target protected environments with required reviewers
   - GitHub enforces reviewers via environment protection rules configured in repo settings (policy validates environment usage, not the settings themselves)

2. **Data Leakage Prevention**: Detect potential PII in logs
   - Warns if echoing customer/user/email/phone data

3. **Container Scanning**: Docker builds must include Trivy scanning
   - Ensures vulnerability scanning for all container images

### Best Practice Policies

1. **Concurrency Control**: Workflows with `push` trigger should have concurrency
   - Prevents redundant runs and saves CI minutes

2. **Timeout Minutes**: All jobs must have `timeout-minutes`
   - Prevents hung workflows

3. **Reusable Workflows**: Should use `workflow_call` trigger
   - Ensures proper reusable workflow pattern

4. **Deploy Environments**: Deploy workflows should use GitHub environments
   - Enables protection rules and deployment history

## Usage

### Local Validation

```bash
# Install Conftest (see https://www.conftest.dev/install/ for your OS/package manager)
# macOS example: brew install conftest

# Validate a single workflow
conftest test .github/workflows/ci.yml --policy .github/policies

# Validate all workflows
conftest test .github/workflows/*.yml --policy .github/policies
```

### CI Validation

Policies are automatically enforced via `.github/workflows/policy-validation.yml` on:
- Every PR that modifies workflows
- Every push to `main`
- Manual trigger via `workflow_dispatch`

### Adding New Policies

1. Edit `.github/policies/workflows.rego`
2. Add `deny[msg]` rule for errors or `warn[msg]` for warnings
3. Test locally with Conftest
4. Create PR - policy validation will run automatically

## Policy Rules Reference

### deny[msg] - Errors (Block PR)

| Rule | Description | Fix |
|------|-------------|-----|
| SHA pinning | Actions must use commit SHA | Use `@abc123...` instead of `@v4` |
| Hardcoded secrets | No secrets in workflow files | Use `${{secrets.*}}` |
| Explicit permissions | Must have permissions block | Add `permissions: { ... }` |
| Timeout minutes | Jobs must have timeout | Add `timeout-minutes: 30` |
| Concurrency control | Push triggers need concurrency | Add `concurrency: { ... }` |
| Container scanning | Docker builds need Trivy | Add Trivy scanning step |
| pull_request_target guard | Every job needs `if:` when using pull_request_target | Add a guard, e.g. `if: github.event.pull_request.head.repo.fork == false` |
| write-all permissions | Do not use write-all permissions | Specify explicit permissions at workflow/job level |

### warn[msg] - Warnings (Non-blocking)

| Rule | Description | Recommendation |
|------|-------------|----------------|
| Reusable trigger | Reusable should use workflow_call | Add `on: workflow_call` |
| Deploy environments | Deploys should use environments | Add `environment: production` |
| Echo PII | Avoid echoing sensitive data (customer/email/phone/SSN/credit card) | Remove/obfuscate PII in logs |

## Compliance Mapping

### SOC2 Controls

- **CC6.1** (Logical Access): Explicit permissions; production approvals enforced via GitHub environments (configured in repo settings)
- **CC6.6** (Vulnerability Management): Container scanning, SHA pinning
- **CC7.2** (Change Management): Concurrency control; production deployments should use protected environments
- **CC7.3** (Data Protection): Data leakage prevention

### SLSA Framework

- **Level 2**: SHA pinning (immutable dependencies)
- **Level 3**: Container signing (Cosign), SBOM generation
- **Level 4**: Policy enforcement (contributes to higher SLSA levels)

## Exemptions

To exempt a workflow from specific policies (use sparingly):

```yaml
# Add comment explaining exemption
# POLICY_EXEMPT: sha-pinning
# Reason: Using local action in same repo
uses: ./.github/actions/custom-action
```

Note: The exemption mechanism is planned but not yet implemented. Currently, all policies are enforced without exception.

## Troubleshooting

### Policy Validation Fails

1. Check workflow summary for specific violations
2. Review `.github/policies/workflows.rego` for rule details
3. Run `conftest test` locally to debug
4. Fix violations and push again

### False Positives

If a policy incorrectly flags valid code:
1. Review the Rego rule logic
2. Add exception handling if needed
3. Create PR to update policy

## References

- [Open Policy Agent](https://www.openpolicyagent.org/)
- [Conftest](https://www.conftest.dev/)
- [GitHub Actions Security](https://docs.github.com/en/actions/security-guides)
- [WARP CI/CD Standards](../../docs/MERGLBOT_GITHUB_ACTIONS_GLOBAL_RULES.md)
