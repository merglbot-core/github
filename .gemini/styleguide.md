# Merglbot Code Review Style Guide

> **Purpose**: This document provides context for Gemini Code Assist when reviewing PRs in Merglbot repositories.

---

## 🏢 Project Context

**Merglbot** is an AI-powered code assistant platform built with:
- **Frontend**: React 18, TypeScript, MUI v6, ECharts
- **Backend**: Cloud Run, Firestore, BigQuery
- **Infrastructure**: Terraform, GitHub Actions, GCP
- **AI Models**: Claude, GPT, Gemini

---

## 🔒 Security Rules (CRITICAL)

### Must Check
1. **No hardcoded secrets** - API keys, tokens, passwords must use `${{ secrets.* }}`
2. **No SA JSON keys** - Always use Workload Identity Federation (WIF/OIDC)
3. **SHA-pinned actions** - GitHub Actions must use full SHA, not tags
4. **No credentials in logs** - Never log sensitive data

### Auth Patterns
- **Current**: Session-based v1 (OAuth + Firestore)
- **Verify**: Auth changes must follow `AUTHENTICATION_AUTHORIZATION.md`

---

## 📝 Code Quality Standards

### TypeScript/JavaScript
```typescript
// ✅ GOOD: Explicit types, proper error handling
async function fetchData(id: string): Promise<Data | null> {
  try {
    const response = await api.get(`/data/${id}`);
    return response.data;
  } catch (error) {
    logger.error('Failed to fetch data', { id, error });
    return null;
  }
}

// ❌ BAD: Any types, no error handling
async function fetchData(id) {
  return await api.get(`/data/${id}`);
}
```

### Python
```python
# ✅ GOOD: Type hints, docstrings
def process_data(items: list[dict]) -> dict:
    """Process list of items and return summary."""
    return {"count": len(items)}

# ❌ BAD: No types, no docs
def process_data(items):
    return {"count": len(items)}
```

### Terraform
```hcl
# ✅ GOOD: Labels, descriptions
resource "google_cloud_run_service" "api" {
  name     = "merglbot-api"
  location = var.region

  metadata {
    labels = {
      environment = var.environment
      managed_by  = "terraform"
    }
  }
}
```

---

## 🔄 GitHub Actions Standards

### Required Patterns
```yaml
# ✅ SHA-pinned actions
- uses: actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683  # v4.2.2

# ✅ Proper permissions
permissions:
  contents: read
  id-token: write  # For WIF

# ✅ Timeouts and concurrency
timeout-minutes: 30
concurrency:
  group: ${{ github.workflow }}-${{ github.ref }}
  cancel-in-progress: true
```

### Forbidden Patterns
```yaml
# ❌ Tag-only references (security risk)
- uses: actions/checkout@v4

# ❌ Overly permissive
permissions: write-all

# ❌ Hardcoded credentials
env:
  API_KEY: "sk-12345..."
```

---

## 📋 PR Review Checklist

When reviewing PRs, verify:

### Security
- [ ] No secrets in code or logs
- [ ] WIF/OIDC used (no SA JSON)
- [ ] Actions are SHA-pinned
- [ ] Proper permissions scope

### Architecture
- [ ] Follows existing patterns
- [ ] No unnecessary complexity
- [ ] Proper error handling
- [ ] Logging is appropriate

### Testing
- [ ] Tests for new functionality
- [ ] No regressions
- [ ] Edge cases covered

### Documentation
- [ ] Code is self-documenting
- [ ] Complex logic has comments
- [ ] API changes documented

---

## 🌍 Language Guidelines

- **Review comments**: Czech (preferred) or English
- **Code comments**: English only
- **Commit messages**: English, conventional format (`feat:`, `fix:`, `docs:`, `ci:`)

---

## 📚 Reference Documentation

For detailed standards, refer to:

| Document | Purpose |
|----------|---------|
| `RULEBOOK_V2.md` | Platform rules (SSOT) |
| `MERGLBOT_GLOBAL_RULES.txt` | Agent rules |
| `SECURITY.md` | Security policy |
| `AUTHENTICATION_AUTHORIZATION.md` | Auth patterns |
| `MERGLBOT_GITHUB_ACTIONS_GLOBAL_RULES.md` | CI/CD standards |

---

## ⚠️ Common Issues to Flag

### Critical (Block PR)
- Hardcoded secrets
- SA JSON keys in code
- Missing auth on endpoints
- SQL injection vulnerabilities

### High Priority
- Unvalidated user input
- Missing error handling
- Overly permissive CORS
- Sensitive data in logs

### Medium Priority
- Missing types
- No tests for changes
- Inconsistent naming
- Missing documentation

### Low Priority
- Code style issues
- Verbose code
- Minor optimizations

---

## 🎯 Review Output Format

When providing review feedback, use this structure:

```markdown
## Summary
[2-3 sentences summarizing the PR and overall assessment]

## Findings

### 🔴 Critical
[List critical issues that must be fixed]

### 🟡 High Priority
[List high priority issues]

### 🟢 Suggestions
[List optional improvements]

## Verdict
[APPROVE / CHANGES NEEDED / BLOCK]
```

---

**Version**: 1.0  
**Last Updated**: 2025-11-25  
**Maintainer**: Platform Team
