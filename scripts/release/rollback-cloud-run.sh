#!/usr/bin/env bash

# Cloud Run Rollback Utility Script
# Usage: ./rollback-cloud-run.sh <service-name> [revision] [--project=<project>] [--region=<region>]

set -euo pipefail

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Default values
PROJECT_ID="${GCP_PROJECT_ID:-}"
REGION="${GCP_REGION:-europe-west1}"
SERVICE_NAME=""
TARGET_REVISION=""
DRY_RUN=false
CONFIRM=true

# Function to print colored output
print_color() {
    local color=$1
    shift
    echo -e "${color}$*${NC}"
}

# Function to print usage
usage() {
    cat << EOF
Usage: $0 <service-name> [revision] [options]

Rollback a Cloud Run service to a previous revision.

Arguments:
    service-name    Name of the Cloud Run service
    revision        Specific revision to rollback to (optional)
                   If not provided, will rollback to previous revision

Options:
    --project=PROJECT_ID    GCP Project ID (default: \$GCP_PROJECT_ID)
    --region=REGION        GCP Region (default: europe-west1)
    --dry-run              Show what would be done without executing
    --no-confirm           Skip confirmation prompt
    --help                 Show this help message

Examples:
    # Rollback to previous revision
    $0 btf-api

    # Rollback to specific revision
    $0 btf-api btf-api-00003-abc

    # Rollback with specific project and region
    $0 btf-api --project=mb-portal-prd --region=us-central1

EOF
    exit 0
}

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --help)
            usage
            ;;
        --project=*)
            PROJECT_ID="${1#*=}"
            shift
            ;;
        --region=*)
            REGION="${1#*=}"
            shift
            ;;
        --dry-run)
            DRY_RUN=true
            shift
            ;;
        --no-confirm)
            CONFIRM=false
            shift
            ;;
        *)
            if [[ -z "$SERVICE_NAME" ]]; then
                SERVICE_NAME="$1"
            elif [[ -z "$TARGET_REVISION" ]]; then
                TARGET_REVISION="$1"
            else
                print_color "$RED" "Error: Unknown argument: $1"
                usage
            fi
            shift
            ;;
    esac
done

# Validate required arguments
if [[ -z "$SERVICE_NAME" ]]; then
    print_color "$RED" "Error: Service name is required"
    usage
fi

if [[ -z "$PROJECT_ID" ]]; then
    print_color "$RED" "Error: Project ID is required. Set GCP_PROJECT_ID or use --project"
    exit 1
fi

# Function to get service info
get_service_info() {
    print_color "$BLUE" "📊 Fetching service information..."
    
    if ! gcloud run services describe "$SERVICE_NAME" \
        --project="$PROJECT_ID" \
        --region="$REGION" \
        --format=json > /tmp/service-info.json 2>/dev/null; then
        print_color "$RED" "Error: Failed to get service info. Service may not exist."
        exit 1
    fi
    
    # Get current revision
    CURRENT_REVISION=$(jq -r '.status.latestReadyRevisionName' /tmp/service-info.json)
    print_color "$GREEN" "Current revision: $CURRENT_REVISION"
    
    # Get traffic allocations
    print_color "$BLUE" "\n📊 Current traffic allocation:"
    jq -r '.status.traffic[] | "\(.revisionName // "LATEST"): \(.percent)%"' /tmp/service-info.json
}

# Function to get revision list
get_revisions() {
    print_color "$BLUE" "\n📋 Available revisions:"
    
    gcloud run revisions list \
        --service="$SERVICE_NAME" \
        --project="$PROJECT_ID" \
        --region="$REGION" \
        --format="table(name,status.conditions[0].status:label=READY,metadata.creationTimestamp)" \
        --limit=10
}

# Function to determine target revision
determine_target_revision() {
    if [[ -z "$TARGET_REVISION" ]]; then
        print_color "$YELLOW" "\n🔍 No target revision specified, finding previous revision..."
        
        # Get list of revisions sorted by creation time (newest first)
        REVISIONS=$(gcloud run revisions list \
            --service="$SERVICE_NAME" \
            --project="$PROJECT_ID" \
            --region="$REGION" \
            --format="value(name)" \
            --limit=5)
        
        # Find the previous revision (skip the current one)
        for rev in $REVISIONS; do
            if [[ "$rev" != "$CURRENT_REVISION" ]]; then
                TARGET_REVISION="$rev"
                break
            fi
        done
        
        if [[ -z "$TARGET_REVISION" ]]; then
            print_color "$RED" "Error: Could not find a previous revision to rollback to"
            exit 1
        fi
        
        print_color "$GREEN" "Selected target revision: $TARGET_REVISION"
    fi
}

# Function to perform rollback
perform_rollback() {
    print_color "$YELLOW" "\n🔄 Rolling back service..."
    print_color "$BLUE" "From: $CURRENT_REVISION"
    print_color "$BLUE" "To:   $TARGET_REVISION"
    
    if [[ "$DRY_RUN" == "true" ]]; then
        print_color "$YELLOW" "\n[DRY RUN] Would execute:"
        echo "gcloud run services update-traffic $SERVICE_NAME \\"
        echo "    --to-revisions=$TARGET_REVISION=100 \\"
        echo "    --project=$PROJECT_ID \\"
        echo "    --region=$REGION"
        return
    fi
    
    # Confirm before rollback
    if [[ "$CONFIRM" == "true" ]]; then
        print_color "$YELLOW" "\n⚠️  WARNING: This will redirect 100% traffic to $TARGET_REVISION"
        read -p "Are you sure you want to proceed? (yes/no): " -r
        if [[ ! "$REPLY" =~ ^[Yy]es$ ]]; then
            print_color "$RED" "Rollback cancelled"
            exit 0
        fi
    fi
    
    # Perform the rollback
    if gcloud run services update-traffic "$SERVICE_NAME" \
        --to-revisions="$TARGET_REVISION=100" \
        --project="$PROJECT_ID" \
        --region="$REGION"; then
        print_color "$GREEN" "\n✅ Rollback successful!"
    else
        print_color "$RED" "\n❌ Rollback failed!"
        exit 1
    fi
}

# Function to verify rollback
verify_rollback() {
    if [[ "$DRY_RUN" == "true" ]]; then
        return
    fi
    
    print_color "$BLUE" "\n🔍 Verifying rollback..."
    
    # Wait a moment for changes to propagate
    sleep 5
    
    # Get updated service info
    gcloud run services describe "$SERVICE_NAME" \
        --project="$PROJECT_ID" \
        --region="$REGION" \
        --format=json > /tmp/service-info-after.json
    
    # Check traffic allocation
    TRAFFIC=$(jq -r '.status.traffic[] | select(.percent == 100) | .revisionName' /tmp/service-info-after.json)
    
    if [[ "$TRAFFIC" == "$TARGET_REVISION" ]]; then
        print_color "$GREEN" "✅ Traffic successfully redirected to $TARGET_REVISION"
    else
        print_color "$RED" "⚠️  Traffic verification failed. Please check manually."
    fi
    
    # Get service URL
    SERVICE_URL=$(jq -r '.status.url' /tmp/service-info-after.json)
    print_color "$BLUE" "\n📌 Service URL: $SERVICE_URL"
    
    # Check service health (if endpoint exists)
    if [[ -n "$SERVICE_URL" ]]; then
        print_color "$BLUE" "\n🏥 Checking service health..."
        if curl -sf "$SERVICE_URL/health" > /dev/null 2>&1; then
            print_color "$GREEN" "✅ Health check passed"
        else
            print_color "$YELLOW" "⚠️  Health check endpoint not available or failed"
        fi
    fi
}

# Function to create rollback record
create_rollback_record() {
    if [[ "$DRY_RUN" == "true" ]]; then
        return
    fi
    
    TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
    ROLLBACK_FILE="/tmp/rollback-$SERVICE_NAME-$TIMESTAMP.json"
    
    cat > "$ROLLBACK_FILE" << EOF
{
    "timestamp": "$TIMESTAMP",
    "service": "$SERVICE_NAME",
    "project": "$PROJECT_ID",
    "region": "$REGION",
    "from_revision": "$CURRENT_REVISION",
    "to_revision": "$TARGET_REVISION",
    "performed_by": "$(whoami)",
    "reason": "Manual rollback via script"
}
EOF
    
    print_color "$BLUE" "\n📝 Rollback record saved to: $ROLLBACK_FILE"
}

# Main execution
main() {
    print_color "$BLUE" "🚀 Cloud Run Rollback Utility"
    print_color "$BLUE" "============================\n"
    
    # Get service information
    get_service_info
    
    # Show available revisions
    get_revisions
    
    # Determine target revision
    determine_target_revision
    
    # Perform rollback
    perform_rollback
    
    # Verify rollback
    verify_rollback
    
    # Create rollback record
    create_rollback_record
    
    print_color "$GREEN" "\n✨ Rollback process completed!"
    
    # Print next steps
    if [[ "$DRY_RUN" != "true" ]]; then
        print_color "$YELLOW" "\n📋 Next steps:"
        print_color "$YELLOW" "1. Monitor service metrics and logs"
        print_color "$YELLOW" "2. Verify application functionality"
        print_color "$YELLOW" "3. Notify team about the rollback"
        print_color "$YELLOW" "4. Create incident report if needed"
    fi
}

# Run main function
main