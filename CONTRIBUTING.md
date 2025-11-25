# Contributing Guidelines

For general contribution guidelines, code standards, and PR requirements, 
please refer to the canonical contributing documentation:

**[Merglbot Contributing Guide](https://github.com/merglbot-public/docs/blob/main/CONTRIBUTING.md)**

## Repository-Specific Guidelines

This repository contains reusable GitHub Actions workflows. When contributing:

### Workflow Standards

- Follow [MERGLBOT_GITHUB_ACTIONS_GLOBAL_RULES.md](https://github.com/merglbot-public/docs/blob/main/MERGLBOT_GITHUB_ACTIONS_GLOBAL_RULES.md)
- Use [MERGLBOT_GITHUB_ACTIONS_REUSABLE_WORKFLOWS.md](https://github.com/merglbot-public/docs/blob/main/MERGLBOT_GITHUB_ACTIONS_REUSABLE_WORKFLOWS.md) patterns
- Validate YAML with `yamllint` and `actionlint` before committing
- Test workflows in a sandbox repo before opening PR

### PR Requirements

- All PRs require at least 1 approval
- CI checks must pass (YAML validation, linting)
- Follow [PR Policy](https://github.com/merglbot-public/docs/blob/main/PR_POLICY.md)
- Use conventional commits format
- Squash merge to main

### Branch Naming

- `feat/` - New workflows or features
- `fix/` - Bug fixes
- `docs/` - Documentation updates
- `ci/` - CI/CD improvements

For more details, see [Rulebook v2](https://github.com/merglbot-public/docs/blob/main/RULEBOOK_V2.md).

