# Security Policy

For security policies, vulnerability reporting procedures, and security best practices, 
please refer to the canonical security documentation:

**[Merglbot Security Policy](https://github.com/merglbot-public/docs/blob/main/SECURITY.md)**

## Quick Reference

- **Report vulnerabilities**: Follow the reporting process in the canonical security policy
- **Security automation**: See [SECURITY_AUTOMATION_TOOLS.md](https://github.com/merglbot-public/docs/blob/main/SECURITY_AUTOMATION_TOOLS.md)
- **Incident response**: Follow [MERGLBOT_SECURITY_INCIDENT_RESPONSE.md](https://github.com/merglbot-public/docs/blob/main/MERGLBOT_SECURITY_INCIDENT_RESPONSE.md)
- **Secrets management**: Follow [MERGLBOT_SECRETS_NAMING_AND_LOGGING.md](https://github.com/merglbot-public/docs/blob/main/MERGLBOT_SECRETS_NAMING_AND_LOGGING.md)

## Repository-Specific Security Notes

This repository contains reusable GitHub Actions workflows for the Merglbot platform. 
Security considerations:

- All workflows use Workload Identity Federation (WIF) for GCP authentication
- No service account JSON keys should be committed
- Secrets are referenced via `${{ secrets.* }}` or `${{ vars.* }}`
- Follow the [GitHub Actions Global Rules](https://github.com/merglbot-public/docs/blob/main/MERGLBOT_GITHUB_ACTIONS_GLOBAL_RULES.md)

