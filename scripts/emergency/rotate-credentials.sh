#!/usr/bin/env bash
# Emergency credential rotation script
# Based on MERGLBOT_GITIGNORE_SECURITY.md standards

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

# Target project for every Secret Manager call. Explicit GCP_PROJECT wins; otherwise the
# active gcloud configuration, which is shown before any change so the operator can abort.
function resolve_gcp_project() {
    local project="${GCP_PROJECT:-}"
    if [ -z "$project" ]; then
        project=$(gcloud config get-value project 2>/dev/null) || project=""
    fi
    if ! [[ "$project" =~ ^[a-z][a-z0-9-]{4,28}[a-z0-9]$ ]]; then
        echo -e "${RED}  ❌ No valid target project (set GCP_PROJECT or gcloud config project)${NC}" >&2
        return 1
    fi
    printf '%s\n' "$project"
}

# Make sure a previous version is ENABLED again and verify it by reading its state back.
# Returns 0 when it was still ENABLED, 2 when it was re-enabled and verified, and 1 when the
# state could not be confirmed (the outcome is printed and logged as unresolved).
function restore_previous_version() {
    local version="$1" secret_name="$2" project="$3" state=""
    state=$(gcloud secrets versions describe "$version" --secret="$secret_name" \
        --project="$project" --format="value(state)") || state=""
    if [ "$state" = "ENABLED" ]; then
        return 0
    fi
    if gcloud secrets versions enable "$version" --secret="$secret_name" \
        --project="$project" >/dev/null; then
        state=$(gcloud secrets versions describe "$version" --secret="$secret_name" \
            --project="$project" --format="value(state)") || state=""
        if [ "$state" = "ENABLED" ]; then
            return 2
        fi
    fi
    echo -e "${RED}  ❌ RECOVERY UNRESOLVED: previous version ${version##*/} state is ${state:-unknown}; re-enable it manually and verify its state${NC}" >&2
    log_incident "ERROR: Previous version ${version##*/} of secret: $secret_name may be disabled (state ${state:-unknown}); manual recovery required"
    return 1
}

# Rotation order (recoverable): prepare the new value, add it as a new version, verify the
# new version is ENABLED, and only then disable the previous version. Any failure before the
# disable leaves the previous version untouched; an interruption or an ambiguous disable error
# restores the previous version and verifies it. The secret value is never echoed and lives
# only in a 0700 temp directory.
function rotate_gcp_secret() {
    local secret_name="$1"
    echo -e "${YELLOW}Rotating GCP secret: $secret_name${NC}"

    if [ "$DRY_RUN" == "true" ]; then
        echo "  [DRY RUN] Would add a new version, verify it is ENABLED, then disable the previous version"
        return
    fi

    # Validate secret name format (prevent command injection)
    if ! [[ "$secret_name" =~ ^[a-zA-Z0-9_-]+$ ]]; then
        echo -e "${RED}  ❌ Invalid secret name format. Only alphanumeric characters, hyphens, and underscores allowed.${NC}"
        # Log incident without revealing potentially sensitive input
        log_incident "ERROR: Invalid secret name format attempted (input sanitized)"
        return 1
    fi

    local project
    project=$(resolve_gcp_project) || return 1
    echo "  Target project: $project"

    # Get latest enabled version with proper error handling
    local current_version
    if ! current_version=$(gcloud secrets versions list "$secret_name" \
        --project="$project" \
        --filter="state:ENABLED" \
        --limit=1 \
        --format="value(name)"); then
        echo -e "${RED}  ❌ Failed to list versions for secret: $secret_name${NC}"
        log_incident "ERROR: Failed to list versions for secret: $secret_name"
        return 1
    fi
    if [ -z "$current_version" ]; then
        echo -e "${YELLOW}  ⚠️  No enabled version found for $secret_name${NC}"
        log_incident "Warning: No enabled version found for secret: $secret_name"
    fi

    # Prepare the new value before touching any version. umask 077 makes the directory and
    # the file private at creation time (no chmod on a not-yet-existing file).
    local secure_dir secret_file
    secure_dir=$(umask 077 && mktemp -d -t rotate_secret_dir.XXXXXX) || {
        echo -e "${RED}  ❌ Failed to create temporary directory${NC}"
        log_incident "ERROR: Failed to create temporary directory"
        return 1
    }
    secret_file="${secure_dir}/secret.tmp"
    if ! (umask 077 && openssl rand -base64 32 > "$secret_file") || [ ! -s "$secret_file" ]; then
        echo -e "${RED}  ❌ Failed to generate the new value; no version was changed${NC}"
        log_incident "ERROR: Failed to generate new value for secret: $secret_name (no change)"
        rm -rf "$secure_dir"
        return 1
    fi

    # Add the new version from the file (avoids exposing the value in the process list).
    local new_version add_rc=0
    new_version=$(gcloud secrets versions add "$secret_name" --project="$project" \
        --data-file="$secret_file" --format="value(name)") || add_rc=$?
    shred -u "$secret_file" 2>/dev/null || rm -f "$secret_file"
    rm -rf "$secure_dir"
    if [ "$add_rc" -ne 0 ] || [ -z "$new_version" ]; then
        echo -e "${RED}  ❌ Failed to add new version; previous version left enabled${NC}"
        log_incident "ERROR: Failed to add new version to secret: $secret_name (previous version untouched)"
        return 1
    fi
    echo -e "${GREEN}  ✅ Added new version ${new_version##*/}${NC}"
    log_incident "Added new version ${new_version##*/} to secret: $secret_name"

    local new_state=""
    new_state=$(gcloud secrets versions describe "$new_version" --secret="$secret_name" \
        --project="$project" --format="value(state)") || new_state=""
    if [ "$new_state" != "ENABLED" ]; then
        echo -e "${RED}  ❌ New version is not ENABLED (state: ${new_state:-unknown}); previous version left enabled${NC}"
        log_incident "ERROR: New version of $secret_name not ENABLED; previous version untouched"
        return 1
    fi

    if [ -z "$current_version" ]; then
        return 0
    fi

    # From here on an interruption must not leave the secret without the previous version.
    trap 'restore_rc=0; restore_previous_version "$current_version" "$secret_name" "$project" || restore_rc=$?; if [ "$restore_rc" -ne 1 ]; then echo -e "${YELLOW}  ↩️  Interrupted: previous version verified ENABLED${NC}" >&2; fi; exit 130' INT TERM
    if gcloud secrets versions disable "$current_version" --secret="$secret_name" --project="$project"; then
        trap - INT TERM
        echo -e "${GREEN}  ✅ Disabled previous version ${current_version##*/}${NC}"
        log_incident "Disabled previous version ${current_version##*/} of secret: $secret_name"
        return 0
    fi
    # A nonzero exit does not prove the disable was not applied (for example a transport error
    # after the server committed it). Read the real state and restore the previous version.
    local restore_rc=0
    restore_previous_version "$current_version" "$secret_name" "$project" || restore_rc=$?
    trap - INT TERM
    case "$restore_rc" in
        0)
            echo -e "${RED}  ❌ Failed to disable previous version; verified it is still ENABLED, both versions stay enabled (no outage)${NC}"
            log_incident "ERROR: Failed to disable previous version of secret: $secret_name (previous verified ENABLED, both enabled)"
            ;;
        2)
            echo -e "${YELLOW}  ↩️  Disable reported an error; previous version ${current_version##*/} re-enabled and verified, both versions enabled${NC}"
            log_incident "ERROR: Failed to disable previous version of secret: $secret_name (previous re-enabled and verified, both enabled)"
            ;;
    esac
    return 1
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

# Sourcing the file (tests) only defines the functions.
if [[ "${BASH_SOURCE[0]}" != "$0" ]]; then
    return 0
fi

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
