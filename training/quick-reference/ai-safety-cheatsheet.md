# AI Safety Quick Reference

## ✅ SAFE to share with AI tools
- Code logic, algorithms, refactor requests
- File names, function signatures
- Secret NAMES only (e.g., ANTHROPIC_API_KEY) – never values
- Public docs, MERGLBOT_*.md, anonymized data

## ❌ NEVER share
- Secret VALUES (API keys, tokens, passwords)
- Customer data, PII, internal URLs
- Full .env files, credentials.json contents
- Production database connection strings

## Secure patterns
- Use placeholders: {{SECRET_NAME}} instead of real values
- Run secret scanners (git-secrets, ripgrep) before commits
- Keep AI-assisted changes under human review (no auto-merge)

## Incident quick steps
1) Audit logs for unauthorized access
2) Revoke/rotate secret immediately
3) Notify #security and create incident report
4) Post-mortem → update policy
