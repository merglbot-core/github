# GitHub Token Exposure Response (SEC-P0-005)

**Scope:** Any GitHub token (PAT, fine‑grained PAT, GitHub App token, `gh` CLI token) that was printed to logs/terminal/chat must be treated as compromised.

## Immediate actions (do first)

1. **Revoke the token** in GitHub settings (or app installation settings).
2. **Rotate/replace any downstream credentials** that depended on it (CI secrets, automation, integrations).
3. **Re-authenticate** with least privilege:
   - Prefer GitHub App + fine‑grained permissions when possible.
   - Avoid broad scopes like `admin:*` unless strictly required.

## Audit / forensics (minimum viable)

- Review GitHub audit logs for the exposure window:
  - Look for unexpected org/repo changes (members, secrets, branch protection, workflows, permissions).
  - Check unusual workflow runs / new workflow files / edits to `.github/workflows/**`.

> Note: Audit log API availability depends on org/enterprise plan and privileges; if REST access is unavailable, use the GitHub UI audit log.

## Prevention (don’t repeat)

- Never echo tokens or export them into logs.
- Prefer `GITHUB_TOKEN` in workflows (least-privilege per job), and WIF/OIDC for cloud auth.
- Add/keep policy-as-code checks that fail on hardcoded secrets or unpinned action refs.

## Related tooling

- Org settings baseline: `scripts/audit/apply-org-settings.sh`
- Emergency rotation helper: `scripts/emergency/rotate-credentials.sh`
