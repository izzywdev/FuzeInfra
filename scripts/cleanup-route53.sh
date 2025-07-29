#!/bin/bash
# Script to clean up duplicate Route53 hosted zones for fuzefront.com
# Since the domain is managed in Cloudflare, these zones are not needed

set -euo pipefail

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

log() {
    echo -e "${BLUE}[$(date +'%Y-%m-%d %H:%M:%S')] $1${NC}"
}

warn() {
    echo -e "${YELLOW}[WARNING] $1${NC}"
}

error() {
    echo -e "${RED}[ERROR] $1${NC}"
    exit 1
}

success() {
    echo -e "${GREEN}[SUCCESS] $1${NC}"
}

# Check if AWS CLI is available
if ! command -v aws &> /dev/null; then
    error "AWS CLI is not installed or not available"
fi

# Check AWS credentials
if ! aws sts get-caller-identity >/dev/null 2>&1; then
    error "AWS credentials not configured or invalid"
fi

log "Cleaning up duplicate Route53 hosted zones for fuzefront.com..."

# Get all fuzefront.com hosted zones
HOSTED_ZONES=$(aws route53 list-hosted-zones --query 'HostedZones[?Name==`fuzefront.com.`].Id' --output text)

if [[ -z "$HOSTED_ZONES" ]]; then
    log "No Route53 hosted zones found for fuzefront.com"
    exit 0
fi

# Count zones
ZONE_COUNT=$(echo "$HOSTED_ZONES" | wc -w)
log "Found $ZONE_COUNT hosted zones for fuzefront.com"

# List all zones with details
log "Hosted zones found:"
aws route53 list-hosted-zones --query 'HostedZones[?Name==`fuzefront.com.`].{Id:Id,Records:ResourceRecordSetCount,Comment:Config.Comment}' --output table

# Warning before deletion
warn "⚠️  These Route53 hosted zones will be deleted because:"
warn "    - fuzefront.com is managed in Cloudflare, not Route53"
warn "    - Multiple duplicate zones exist from Terraform runs"
warn "    - DNS will be handled by Cloudflare going forward"
echo ""

# Confirmation prompt
read -p "Are you sure you want to delete ALL Route53 hosted zones for fuzefront.com? (yes/no): " -r
echo ""

if [[ ! $REPLY =~ ^[Yy][Ee][Ss]$ ]]; then
    log "Operation cancelled by user"
    exit 0
fi

# Delete each hosted zone
DELETED_COUNT=0
FAILED_COUNT=0

for zone_id in $HOSTED_ZONES; do
    log "Processing zone: $zone_id"
    
    # Get all records in the zone (except NS and SOA which can't be deleted)
    RECORDS=$(aws route53 list-resource-record-sets --hosted-zone-id "$zone_id" \
        --query 'ResourceRecordSets[?Type!=`NS` && Type!=`SOA`]' \
        --output json 2>/dev/null || echo "[]")
    
    RECORD_COUNT=$(echo "$RECORDS" | jq length)
    
    if [[ $RECORD_COUNT -gt 0 ]]; then
        log "Deleting $RECORD_COUNT non-default records from zone $zone_id..."
        
        # Create change batch to delete all records
        CHANGE_BATCH=$(echo "$RECORDS" | jq '{
            Changes: [
                .[] | {
                    Action: "DELETE",
                    ResourceRecordSet: .
                }
            ]
        }')
        
        # Execute the change batch
        if aws route53 change-resource-record-sets \
            --hosted-zone-id "$zone_id" \
            --change-batch "$CHANGE_BATCH" >/dev/null 2>&1; then
            log "✅ Records deleted from zone $zone_id"
        else
            warn "⚠️  Failed to delete some records from zone $zone_id (they may not exist)"
        fi
        
        # Wait a moment for changes to propagate
        sleep 2
    fi
    
    # Delete the hosted zone
    log "Deleting hosted zone: $zone_id"
    if aws route53 delete-hosted-zone --id "$zone_id" >/dev/null 2>&1; then
        success "✅ Deleted hosted zone: $zone_id"
        ((DELETED_COUNT++))
    else
        error "❌ Failed to delete hosted zone: $zone_id"
        ((FAILED_COUNT++))
    fi
    
    # Brief pause between deletions
    sleep 1
done

# Summary
log ""
log "🎉 Route53 cleanup completed!"
log "📊 Summary:"
log "   ✅ Zones deleted: $DELETED_COUNT"
if [[ $FAILED_COUNT -gt 0 ]]; then
    log "   ❌ Zones failed: $FAILED_COUNT"
fi
log ""
log "✨ fuzefront.com DNS is now managed exclusively by Cloudflare"
log "🔗 infra.fuzefront.com will be created via Terraform + Cloudflare"