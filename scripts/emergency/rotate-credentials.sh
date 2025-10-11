#!/usr/bin/env bash
# Emergency credential rotation script
# Based on WARP_GITIGNORE_SECURITY.md standards

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# Configuration
INCIDENT_LOG="${INCIDENT_LOG:-./SECURITY_INCIDENTS.md}"
DRY_RUN="${DRY_RUN:-false}"

function log_incident() {
    local message="$1"
    echo -e "${BLUE}[$(date '+%Y-%m-%d %H:%M:%S')]${NC} $message"
    
    if [ "$DRY_RUN" != "true" ]; then
        echo "- $(date '+%Y-%m-%d %H:%M:%S'): $message" >> "$INCIDENT_LOG"
    fi
}

function rotate_gcp_secret() {
    local secret_name="$1"
    echo -e "${YELLOW}Rotating GCP secret: $secret_name${NC}"
    
    if [ "$DRY_RUN" == "true" ]; then
        echo "  [DRY RUN] Would disable current version and add new version"
        return
    fi
    
    # Disable current version
    current_version=$(gcloud secrets versions list "$secret_name" --limit=1 --format="value(name)" | head -1)
    if gcloud secrets versions disable "$current_version" --secret="$secret_name" 2>/dev/null; then
        echo -e "${GREEN}  ✅ Disabled current version${NC}"
    else
        echo -e "${RED}  ❌ Failed to disable current version${NC}"
    fi
    
    # Generate new secret value
    NEW_SECRET=$(openssl rand -base64 32)
    echo "$NEW_SECRET" | gcloud secrets versions add "$secret_name" --data-file=-
    
    echo -e "${GREEN}  ✅ Added new version${NC}"
    log_incident "Rotated GCP secret: $secret_name"
}

function rotate_github_token() {
    local token_name="$1"
    echo -e "${YELLOW}Rotating GitHub token: $token_name${NC}"
    
    if [ "$DRY_RUN" == "true" ]; then
        echo "  [DRY RUN] Would revoke and regenerate GitHub token"
        return
    fi
    
    echo "  Please manually revoke the token at: https://github.com/settings/tokens"
    echo "  Token name: $token_name"
    read -p "  Press Enter when completed..."
    
    log_incident "Rotated GitHub token: $token_name"
}

function scan_git_history() {
    local search_term="$1"
    echo -e "${YELLOW}Scanning git history for: $search_term${NC}"
    
    if git log --all --full-history --grep="$search_term" --oneline; then
        echo -e "${RED}  ⚠️  Found in commit history${NC}"
        log_incident "Found '$search_term' in git history"
    else
        echo -e "${GREEN}  ✅ Not found in commit history${NC}"
    fi
}

function main() {
    echo -e "${RED}╔════════════════════════════════════════╗${NC}"
    echo -e "${RED}║   EMERGENCY CREDENTIAL ROTATION TOOL   ║${NC}"
    echo -e "${RED}╚════════════════════════════════════════╝${NC}"
    echo ""
    
    if [ "$DRY_RUN" == "true" ]; then
        echo -e "${YELLOW}Running in DRY RUN mode - no actual changes will be made${NC}"
        echo ""
    fi
    
    PS3="Select rotation type: "
    options=("GCP Secret" "GitHub Token" "Scan Git History" "Full Rotation (All Secrets)" "Exit")
    
    select opt in "${options[@]}"; do
        case $opt in
            "GCP Secret")
                read -p "Enter secret name: " secret_name
                rotate_gcp_secret "$secret_name"
                ;;
            "GitHub Token")
                read -p "Enter token name/description: " token_name
                rotate_github_token "$token_name"
                ;;
            "Scan Git History")
                read -p "Enter search term (partial secret): " search_term
                scan_git_history "$search_term"
                ;;
            "Full Rotation (All Secrets)")
                echo -e "${RED}⚠️  This will rotate ALL known secrets!${NC}"
                read -p "Are you sure? Type 'ROTATE ALL' to confirm: " confirmation
                
                if [ "$confirmation" == "ROTATE ALL" ]; then
                    # List of known secrets (customize per organization)
                    SECRETS=(
                        "runtime--btf-api--prod--api-key"
                        "runtime--aaas-api--prod--api-key"
                        "runtime--portal--prod--session-key"
                    )
                    
                    for secret in "${SECRETS[@]}"; do
                        rotate_gcp_secret "$secret"
                    done
                    
                    echo -e "${GREEN}✅ Full rotation completed${NC}"
                    log_incident "Completed full credential rotation"
                else
                    echo "Rotation cancelled"
                fi
                ;;
            "Exit")
                break
                ;;
            *)
                echo "Invalid option"
                ;;
        esac
        echo ""
    done
    
    echo -e "${GREEN}✅ Emergency response completed${NC}"
    echo "Incident log: $INCIDENT_LOG"
}

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --dry-run)
            DRY_RUN=true
            shift
            ;;
        --incident-log)
            INCIDENT_LOG="$2"
            shift 2
            ;;
        --help)
            echo "Usage: $0 [--dry-run] [--incident-log FILE]"
            echo ""
            echo "Options:"
            echo "  --dry-run        Run without making actual changes"
            echo "  --incident-log   Path to incident log file (default: ./SECURITY_INCIDENTS.md)"
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            exit 1
            ;;
    esac
done

main