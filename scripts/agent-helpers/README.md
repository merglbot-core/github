# Agent Helper Scripts

CLI fallback scripts for AI agents when MCP servers are unavailable or fail.

## Purpose

AI agents (Cursor native, Claude Code, Codex) have varying access to MCP servers.
These scripts provide reliable CLI-based alternatives that always work.

## Scripts

| Script | Purpose | MCP Fallback For |
|--------|---------|------------------|
| `github-ops.sh` | GitHub operations | GitHub MCP Server |
| `gcp-ops.sh` | GCP operations | N/A (always CLI) |

## Usage

### GitHub Operations

```bash
# List repos in org
./scripts/agent-helpers/github-ops.sh list-repos merglbot-core

# Get PR info
./scripts/agent-helpers/github-ops.sh pr-info merglbot-core/infra 123

# List all Merglbot orgs
./scripts/agent-helpers/github-ops.sh list-all-orgs
```

### GCP Operations

```bash
# List projects
./scripts/agent-helpers/gcp-ops.sh list-projects

# List Cloud Run services
./scripts/agent-helpers/gcp-ops.sh cloud-run-list merglbot-admin-prd

# Production health check
./scripts/agent-helpers/gcp-ops.sh health-check
```

## For AI Agents

When working with Merglbot platform:

1. **Try MCP first** (if available in your environment)
2. **Fall back to these scripts** if MCP fails or is unavailable
3. **Never get stuck** - these CLI tools always work

### Fallback Decision Tree

```
GitHub operation needed?
├─ MCP available? → Use MCP tools
└─ MCP unavailable/failed? → Use ./scripts/agent-helpers/github-ops.sh

GCP operation needed?
└─ Always use ./scripts/agent-helpers/gcp-ops.sh (no MCP for GCP)
```

## Prerequisites

- `gh` CLI installed and authenticated (`gh auth login`)
- `gcloud` CLI installed and authenticated (`gcloud auth login`)
- Access to Merglbot GitHub orgs and GCP projects

## Related Documentation

- [MERGLBOT_CURSOR_SETUP.md](https://github.com/merglbot-public/docs/blob/main/MERGLBOT_CURSOR_SETUP.md) - MCP configuration
- [.cursorrules](../../bot-configs/.cursorrules) - Cursor AI rules with fallback strategy
- [MERGLBOT_AI_TOOLS.md](https://github.com/merglbot-public/docs/blob/main/MERGLBOT_AI_TOOLS.md) - AI tools security policy
