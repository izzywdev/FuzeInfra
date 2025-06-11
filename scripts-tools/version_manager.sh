#!/bin/bash

# Mendys Robot Scraper Platform - Version Manager (Linux/Unix)
# Cross-platform shell script version of version_manager.py

set -e  # Exit on any error

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Configuration
VERSION_FILE="version.json"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

# Helper functions
log_info() {
    echo -e "${BLUE}ℹ${NC} $1"
}

log_success() {
    echo -e "${GREEN}✅${NC} $1"
}

log_warning() {
    echo -e "${YELLOW}⚠${NC} $1"
}

log_error() {
    echo -e "${RED}❌${NC} $1"
}

# Check if required tools are available
check_dependencies() {
    local missing_deps=()
    
    if ! command -v jq &> /dev/null; then
        missing_deps+=("jq")
    fi
    
    if ! command -v git &> /dev/null; then
        missing_deps+=("git")
    fi
    
    if [ ${#missing_deps[@]} -ne 0 ]; then
        log_error "Missing required dependencies: ${missing_deps[*]}"
        log_info "Please install missing dependencies:"
        for dep in "${missing_deps[@]}"; do
            case $dep in
                jq)
                    echo "  - Ubuntu/Debian: sudo apt-get install jq"
                    echo "  - macOS: brew install jq"
                    echo "  - CentOS/RHEL: sudo yum install jq"
                    ;;
                git)
                    echo "  - Ubuntu/Debian: sudo apt-get install git"
                    echo "  - macOS: brew install git"
                    echo "  - CentOS/RHEL: sudo yum install git"
                    ;;
            esac
        done
        exit 1
    fi
}

# Parse semantic version
parse_version() {
    local version="$1"
    echo "$version" | sed -E 's/^v?([0-9]+)\.([0-9]+)\.([0-9]+)(-([a-zA-Z]+)\.?([0-9]+))?(\+(.+))?$/\1 \2 \3 \5 \6 \8/'
}

# Get current version
get_current_version() {
    if [ ! -f "$VERSION_FILE" ]; then
        log_error "Version file not found: $VERSION_FILE"
        exit 1
    fi
    
    jq -r '.version' "$VERSION_FILE"
}

# Get version info
get_version_info() {
    local format="${1:-text}"
    
    if [ ! -f "$VERSION_FILE" ]; then
        log_error "Version file not found: $VERSION_FILE"
        exit 1
    fi
    
    case $format in
        json)
            cat "$VERSION_FILE"
            ;;
        text)
            local version=$(jq -r '.version' "$VERSION_FILE")
            local build_number=$(jq -r '.build.number' "$VERSION_FILE")
            local build_date=$(jq -r '.build.date' "$VERSION_FILE")
            local commit=$(jq -r '.build.commit' "$VERSION_FILE")
            local branch=$(jq -r '.build.branch' "$VERSION_FILE")
            
            echo "Current Version: $version"
            echo "Build Number: $build_number"
            echo "Build Date: $build_date"
            echo "Commit: $commit"
            echo "Branch: $branch"
            ;;
        *)
            log_error "Unknown format: $format. Use 'text' or 'json'"
            exit 1
            ;;
    esac
}

# Increment version component
increment_version() {
    local version="$1"
    local component="$2"
    
    read -r major minor patch pre_type pre_num build_meta <<< "$(parse_version "$version")"
    
    case $component in
        major)
            major=$((major + 1))
            minor=0
            patch=0
            pre_type=""
            pre_num=""
            ;;
        minor)
            minor=$((minor + 1))
            patch=0
            pre_type=""
            pre_num=""
            ;;
        patch)
            if [ -n "$pre_type" ]; then
                # Remove pre-release for patch bump
                pre_type=""
                pre_num=""
            else
                patch=$((patch + 1))
            fi
            ;;
        pre-release)
            if [ -n "$pre_type" ]; then
                pre_num=$((pre_num + 1))
            else
                log_error "Cannot bump pre-release on stable version"
                exit 1
            fi
            ;;
        *)
            log_error "Invalid component: $component"
            exit 1
            ;;
    esac
    
    # Construct new version
    local new_version="$major.$minor.$patch"
    if [ -n "$pre_type" ] && [ -n "$pre_num" ]; then
        new_version="$new_version-$pre_type.$pre_num"
    fi
    
    echo "$new_version"
}

# Update version file
update_version_file() {
    local new_version="$1"
    local commit_changes="${2:-false}"
    local create_tag="${3:-false}"
    local push_changes="${4:-false}"
    
    # Get current info
    local build_number=$(jq -r '.build.number' "$VERSION_FILE")
    local current_commit=""
    local current_branch=""
    
    if command -v git &> /dev/null && [ -d .git ]; then
        current_commit=$(git rev-parse --short HEAD 2>/dev/null || echo "")
        current_branch=$(git branch --show-current 2>/dev/null || echo "main")
    fi
    
    # Increment build number
    build_number=$((build_number + 1))
    
    # Update version file
    local temp_file=$(mktemp)
    jq --arg version "$new_version" \
       --arg build_number "$build_number" \
       --arg build_date "$(date -u +%Y-%m-%dT%H:%M:%S.%3NZ)" \
       --arg commit "$current_commit" \
       --arg branch "$current_branch" \
       '.version = $version |
        .build.number = ($build_number | tonumber) |
        .build.date = $build_date |
        .build.commit = $commit |
        .build.branch = $branch |
        .components.backend = $version |
        .components.frontend = $version |
        .components.infrastructure = $version |
        .components.wordpress_sync = $version |
        .metadata.release_date = ($build_date | split("T")[0])' \
       "$VERSION_FILE" > "$temp_file"
    
    mv "$temp_file" "$VERSION_FILE"
    
    log_success "Updated version to $new_version (build #$build_number)"
    
    # Git operations
    if [ "$commit_changes" = "true" ] && command -v git &> /dev/null && [ -d .git ]; then
        git add "$VERSION_FILE"
        git commit -m "chore: bump version to $new_version"
        log_success "Committed version changes"
        
        if [ "$create_tag" = "true" ]; then
            git tag -a "v$new_version" -m "Release v$new_version"
            log_success "Created tag v$new_version"
            
            if [ "$push_changes" = "true" ]; then
                git push origin "$(git branch --show-current)"
                git push origin "v$new_version"
                log_success "Pushed changes and tag to remote"
            fi
        elif [ "$push_changes" = "true" ]; then
            git push origin "$(git branch --show-current)"
            log_success "Pushed changes to remote"
        fi
    fi
}

# Validate version format
validate_version() {
    local version="$1"
    
    if [[ ! $version =~ ^[0-9]+\.[0-9]+\.[0-9]+(-[a-zA-Z]+\.[0-9]+)?(\+.+)?$ ]]; then
        log_error "Invalid version format: $version"
        log_info "Expected format: MAJOR.MINOR.PATCH[-PRE_TYPE.PRE_NUM][+BUILD_META]"
        exit 1
    fi
}

# Show usage
show_usage() {
    echo "Mendys Robot Scraper Platform - Version Manager (Shell Script)"
    echo ""
    echo "Usage: $0 <command> [options]"
    echo ""
    echo "Commands:"
    echo "  current [--format=text|json]     Show current version"
    echo "  info [--format=text|json]        Show detailed version information"
    echo "  bump <component> [options]       Bump version component"
    echo "  set <version> [options]          Set specific version"
    echo "  validate <version>               Validate version format"
    echo ""
    echo "Components:"
    echo "  major                            Bump major version (1.0.0 -> 2.0.0)"
    echo "  minor                            Bump minor version (1.0.0 -> 1.1.0)"
    echo "  patch                            Bump patch version (1.0.0 -> 1.0.1)"
    echo "  pre-release                      Bump pre-release (1.0.0-alpha.1 -> 1.0.0-alpha.2)"
    echo ""
    echo "Options:"
    echo "  --commit                         Commit changes to git"
    echo "  --tag                            Create git tag"
    echo "  --push                           Push changes and tags to remote"
    echo "  --dry-run                        Show what would be done without making changes"
    echo ""
    echo "Examples:"
    echo "  $0 current                       # Show current version"
    echo "  $0 info --format=json           # Show version info as JSON"
    echo "  $0 bump patch                    # Bump patch version"
    echo "  $0 bump minor --commit --tag     # Bump minor and commit with tag"
    echo "  $0 set 2.0.0 --commit --push    # Set version and push to remote"
}

# Main function
main() {
    cd "$PROJECT_ROOT"
    check_dependencies
    
    if [ $# -eq 0 ]; then
        show_usage
        exit 1
    fi
    
    local command="$1"
    shift
    
    # Parse options
    local format="text"
    local commit_changes=false
    local create_tag=false
    local push_changes=false
    local dry_run=false
    
    while [[ $# -gt 0 ]]; do
        case $1 in
            --format=*)
                format="${1#*=}"
                shift
                ;;
            --commit)
                commit_changes=true
                shift
                ;;
            --tag)
                create_tag=true
                shift
                ;;
            --push)
                push_changes=true
                shift
                ;;
            --dry-run)
                dry_run=true
                shift
                ;;
            -h|--help)
                show_usage
                exit 0
                ;;
            *)
                break
                ;;
        esac
    done
    
    case $command in
        current)
            get_current_version
            ;;
        info)
            get_version_info "$format"
            ;;
        bump)
            if [ $# -eq 0 ]; then
                log_error "Missing component for bump command"
                echo "Available components: major, minor, patch, pre-release"
                exit 1
            fi
            
            local component="$1"
            local current_version=$(get_current_version)
            local new_version=$(increment_version "$current_version" "$component")
            
            if [ "$dry_run" = "true" ]; then
                log_info "Would bump $component: $current_version -> $new_version"
            else
                update_version_file "$new_version" "$commit_changes" "$create_tag" "$push_changes"
            fi
            ;;
        set)
            if [ $# -eq 0 ]; then
                log_error "Missing version for set command"
                exit 1
            fi
            
            local new_version="$1"
            validate_version "$new_version"
            
            if [ "$dry_run" = "true" ]; then
                log_info "Would set version to: $new_version"
            else
                update_version_file "$new_version" "$commit_changes" "$create_tag" "$push_changes"
            fi
            ;;
        validate)
            if [ $# -eq 0 ]; then
                log_error "Missing version for validate command"
                exit 1
            fi
            
            validate_version "$1"
            log_success "Version format is valid: $1"
            ;;
        *)
            log_error "Unknown command: $command"
            show_usage
            exit 1
            ;;
    esac
}

# Run main function
main "$@" 