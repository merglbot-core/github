#!/bin/bash
# github-ops.sh - GitHub operations with MCP fallback to CLI
# Usage: ./github-ops.sh <command> [args...]
#
# Commands:
#   list-repos <org>              - List repos in organization
#   pr-info <org/repo> <pr_num>   - Get PR details
#   pr-create <org/repo>          - Create PR (interactive)
#   repo-info <org/repo>          - Get repository info
#
# This script provides CLI fallback for when MCP servers are unavailable.
# Agents should use this to ensure operations never get stuck.

set -euo pipefail

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

log_info() {
    echo -e "${GREEN}[INFO]${NC} $*"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $*"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $*" >&2
}

# Check if gh CLI is available and authenticated
check_gh_auth() {
    if ! command -v gh &> /dev/null; then
        log_error "gh CLI not installed. Install with: brew install gh"
        exit 1
    fi

    if ! gh auth status &> /dev/null; then
        log_error "gh not authenticated. Run: gh auth login"
        exit 1
    fi
}

# List repos in organization
list_repos() {
    local org="${1:?Usage: list-repos <org>}"
    local limit="${2:-50}"

    log_info "Listing repos for $org (limit: $limit)"
    gh repo list "$org" --limit "$limit" --json nameWithOwner,isPrivate,isArchived,updatedAt \
        | jq -r '.[] | "\(.nameWithOwner)\t\(if .isPrivate then "private" else "public" end)\t\(if .isArchived then "ARCHIVED" else "active" end)\t\(.updatedAt)"' \
        | column -t -s $'\t'
}

# Get PR info
pr_info() {
    local repo="${1:?Usage: pr-info <org/repo> <pr_number>}"
    local pr_num="${2:?Usage: pr-info <org/repo> <pr_number>}"

    log_info "Getting PR #$pr_num from $repo"
    gh pr view "$pr_num" --repo "$repo" --json number,title,state,author,createdAt,body,reviews,labels
}

# Create PR
pr_create() {
    local repo="${1:?Usage: pr-create <org/repo>}"
    local title="${2:-}"
    local body="${3:-}"

    if [ -z "$title" ]; then
        log_info "Creating PR for $repo (interactive mode)"
        gh pr create --repo "$repo"
    else
        log_info "Creating PR for $repo: $title"
        gh pr create --repo "$repo" --title "$title" --body "$body"
    fi
}

# Get repository info
repo_info() {
    local repo="${1:?Usage: repo-info <org/repo>}"

    log_info "Getting info for $repo"
    gh repo view "$repo" --json name,description,isPrivate,isArchived,defaultBranchRef,languages,pushedAt
}

# List all Merglbot orgs
list_all_orgs() {
    local orgs=(
        "merglbot-core"
        "merglbot-public"
        "merglbot-denatura"
        "merglbot-proteinaco"
        "merglbot-ruzovyslon"
        "merglbot-extractors"
        "merglbot-autodoplnky"
        "merglbot-hodinarstvibechyne"
        "merglbot-kiteboarding"
        "merglbot-milan-private"
    )

    local total=0
    for org in "${orgs[@]}"; do
        echo "=== $org ==="
        count=$(gh repo list "$org" --limit 100 --json name 2>/dev/null | jq length)
        echo "  Repos: $count"
        total=$((total + count))
    done

    echo ""
    echo "TOTAL: $total repos across ${#orgs[@]} orgs"
}

# Main command dispatcher
main() {
    check_gh_auth

    local cmd="${1:-help}"
    shift || true

    case "$cmd" in
        list-repos)
            list_repos "$@"
            ;;
        pr-info)
            pr_info "$@"
            ;;
        pr-create)
            pr_create "$@"
            ;;
        repo-info)
            repo_info "$@"
            ;;
        list-all-orgs)
            list_all_orgs
            ;;
        help|--help|-h)
            echo "GitHub Operations Helper (MCP CLI Fallback)"
            echo ""
            echo "Usage: $0 <command> [args...]"
            echo ""
            echo "Commands:"
            echo "  list-repos <org> [limit]     - List repos in organization"
            echo "  pr-info <org/repo> <pr_num>  - Get PR details"
            echo "  pr-create <org/repo> [title] - Create PR"
            echo "  repo-info <org/repo>         - Get repository info"
            echo "  list-all-orgs                - List all Merglbot orgs and repo counts"
            echo ""
            echo "Examples:"
            echo "  $0 list-repos merglbot-core"
            echo "  $0 pr-info merglbot-core/infra 123"
            echo "  $0 list-all-orgs"
            ;;
        *)
            log_error "Unknown command: $cmd"
            echo "Run '$0 help' for usage"
            exit 1
            ;;
    esac
}

main "$@"
