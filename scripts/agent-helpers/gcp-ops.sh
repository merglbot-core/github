#!/bin/bash
# gcp-ops.sh - GCP operations helper for AI agents
# Usage: ./gcp-ops.sh <command> [args...]
#
# Commands:
#   list-projects             - List all Merglbot GCP projects
#   cloud-run-list <project>  - List Cloud Run services
#   cloud-run-status <project> <service> - Get service status
#   logs <project> <service>  - Get recent logs
#   org-structure             - Show org/folder structure
#
# GCP operations always use gcloud CLI (no MCP server available).

set -euo pipefail

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log_info() { echo -e "${GREEN}[INFO]${NC} $*"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $*"; }
log_error() { echo -e "${RED}[ERROR]${NC} $*" >&2; }

# Check gcloud auth
check_gcloud_auth() {
    if ! command -v gcloud &> /dev/null; then
        log_error "gcloud CLI not installed"
        exit 1
    fi

    if ! gcloud auth list --filter=status:ACTIVE --format="value(account)" | head -1 | grep -q "@"; then
        log_error "gcloud not authenticated. Run: gcloud auth login"
        exit 1
    fi
}

# List all Merglbot projects
list_projects() {
    log_info "Listing Merglbot GCP projects..."
    gcloud projects list \
        --filter='projectId~"^merglbot-"' \
        --format='table(projectId,name,createTime.date())' \
        --sort-by=projectId
}

# List Cloud Run services
cloud_run_list() {
    local project="${1:?Usage: cloud-run-list <project>}"
    local region="${2:-europe-west1}"

    log_info "Listing Cloud Run services in $project ($region)..."
    gcloud run services list \
        --project="$project" \
        --region="$region" \
        --format='table(metadata.name,status.url,status.latestReadyRevisionName)'
}

# Get Cloud Run service status
cloud_run_status() {
    local project="${1:?Usage: cloud-run-status <project> <service>}"
    local service="${2:?Usage: cloud-run-status <project> <service>}"
    local region="${3:-europe-west1}"

    log_info "Getting status for $service in $project..."
    gcloud run services describe "$service" \
        --project="$project" \
        --region="$region" \
        --format='yaml(status)'
}

# Get recent logs
get_logs() {
    local project="${1:?Usage: logs <project> <service>}"
    local service="${2:?Usage: logs <project> <service>}"
    local limit="${3:-50}"

    log_info "Getting last $limit logs for $service..."
    gcloud logging read \
        "resource.type=cloud_run_revision AND resource.labels.service_name=\"$service\"" \
        --project="$project" \
        --limit="$limit" \
        --format='table(timestamp,severity,textPayload)'
}

# Show org structure
org_structure() {
    log_info "Discovering Merglbot GCP organization structure..."

    # Get org ID
    local org_id
    org_id=$(gcloud organizations list \
        --filter='displayName="merglevsky.cz"' \
        --format='value(ID)' \
        --limit=1 2>/dev/null || echo "")

    if [ -z "$org_id" ]; then
        log_warn "Cannot access organization (requires Org Viewer permissions)"
        log_info "Falling back to project list..."
        list_projects
        return
    fi

    echo "Organization: merglevsky.cz (ID: $org_id)"
    echo ""
    echo "Top-level folders:"
    gcloud resource-manager folders list \
        --organization="$org_id" \
        --format='table(displayName,name)'
}

# Health check for production services
health_check() {
    log_info "Running production health checks..."

    local endpoints=(
        "https://www.merglbot.ai/health"
        "https://admin.merglbot.ai/health"
        "https://www.merglbot.ai/api/viz/denatura/health"
    )

    for url in "${endpoints[@]}"; do
        local status
        status=$(curl -fsS -o /dev/null -w "%{http_code}" "$url" 2>/dev/null || echo "FAIL")
        if [ "$status" = "200" ]; then
            echo -e "${GREEN}✅${NC} $url ($status)"
        else
            echo -e "${RED}❌${NC} $url ($status)"
        fi
    done
}

# Main
main() {
    check_gcloud_auth

    local cmd="${1:-help}"
    shift || true

    case "$cmd" in
        list-projects)
            list_projects
            ;;
        cloud-run-list)
            cloud_run_list "$@"
            ;;
        cloud-run-status)
            cloud_run_status "$@"
            ;;
        logs)
            get_logs "$@"
            ;;
        org-structure)
            org_structure
            ;;
        health-check)
            health_check
            ;;
        help|--help|-h)
            echo "GCP Operations Helper for AI Agents"
            echo ""
            echo "Usage: $0 <command> [args...]"
            echo ""
            echo "Commands:"
            echo "  list-projects                        - List Merglbot GCP projects"
            echo "  cloud-run-list <project> [region]    - List Cloud Run services"
            echo "  cloud-run-status <project> <service> - Get service status"
            echo "  logs <project> <service> [limit]     - Get recent logs"
            echo "  org-structure                        - Show org/folder structure"
            echo "  health-check                         - Check production endpoints"
            echo ""
            echo "Examples:"
            echo "  $0 list-projects"
            echo "  $0 cloud-run-list merglbot-admin-prd"
            echo "  $0 health-check"
            ;;
        *)
            log_error "Unknown command: $cmd"
            exit 1
            ;;
    esac
}

main "$@"
