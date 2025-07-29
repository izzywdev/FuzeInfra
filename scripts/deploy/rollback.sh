#!/bin/bash
# Rollback Script for FuzeInfra
# This script handles rollback operations for production deployments

set -euo pipefail

# Configuration
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
BACKUP_TIMESTAMP="${1:-}"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Logging function
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

# Help function
show_help() {
    cat << EOF
FuzeInfra Rollback Script

Usage: $0 [BACKUP_TIMESTAMP]

Parameters:
  BACKUP_TIMESTAMP    Timestamp of backup to restore (YYYYMMDD_HHMMSS)
                      If not provided, will show available backups

Examples:
  $0                      # Show available backups
  $0 20240129_143022      # Rollback to specific backup

Environment Variables:
  INSTANCE_IP      IP address of target instance
  SSH_KEY_PATH     Path to SSH private key

EOF
}

# Validate prerequisites
validate_prerequisites() {
    log "Validating rollback prerequisites..."
    
    if [[ -z "${INSTANCE_IP:-}" ]]; then
        error "INSTANCE_IP environment variable is required"
    fi
    
    if [[ -z "${SSH_KEY_PATH:-}" ]]; then
        error "SSH_KEY_PATH environment variable is required"
    fi
    
    if [[ ! -f "$SSH_KEY_PATH" ]]; then
        error "SSH key not found at: $SSH_KEY_PATH"
    fi
    
    # Test SSH connectivity
    if ! ssh -o ConnectTimeout=10 -o StrictHostKeyChecking=no -i "$SSH_KEY_PATH" ubuntu@"$INSTANCE_IP" "echo 'SSH connection successful'" >/dev/null 2>&1; then
        error "Unable to connect to instance via SSH"
    fi
    
    success "Prerequisites validation completed"
}

# List available backups
list_backups() {
    log "Available backups on instance $INSTANCE_IP:"
    
    ssh -i "$SSH_KEY_PATH" ubuntu@"$INSTANCE_IP" "
        if [ -d '/opt/fuzeinfra/backups' ]; then
            echo '=== Available Backups ==='
            ls -la /opt/fuzeinfra/backups/ | grep '^d' | while read -r line; do
                backup_dir=\$(echo \"\$line\" | awk '{print \$9}')
                if [[ \"\$backup_dir\" =~ ^[0-9]{8}_[0-9]{6}$ ]]; then
                    backup_size=\$(du -sh /opt/fuzeinfra/backups/\$backup_dir 2>/dev/null | awk '{print \$1}')
                    backup_files=\$(find /opt/fuzeinfra/backups/\$backup_dir -name '*.tar.gz' | wc -l)
                    echo \"📦 \$backup_dir (Size: \$backup_size, Files: \$backup_files)\"
                fi
            done
        else
            echo '❌ No backup directory found'
            exit 1
        fi
    "
}

# Validate backup
validate_backup() {
    local backup_timestamp="$1"
    
    log "Validating backup: $backup_timestamp"
    
    ssh -i "$SSH_KEY_PATH" ubuntu@"$INSTANCE_IP" "
        BACKUP_DIR=\"/opt/fuzeinfra/backups/$backup_timestamp\"
        
        if [ ! -d \"\$BACKUP_DIR\" ]; then
            echo \"❌ Backup directory not found: \$BACKUP_DIR\"
            exit 1
        fi
        
        echo \"📋 Backup validation for: $backup_timestamp\"
        echo \"Location: \$BACKUP_DIR\"
        echo \"Size: \$(du -sh \$BACKUP_DIR | awk '{print \$1}')\"
        
        # Check for volume backups
        VOLUME_BACKUPS=\$(find \$BACKUP_DIR -name '*.tar.gz' | wc -l)
        echo \"Volume backups: \$VOLUME_BACKUPS\"
        
        # Check for config backups
        if [ -d \"\$BACKUP_DIR/config\" ]; then
            echo \"✅ Configuration backup found\"
        else
            echo \"⚠️  No configuration backup found\"
        fi
        
        # Check for environment backup
        if [ -f \"\$BACKUP_DIR/.env\" ]; then
            echo \"✅ Environment backup found\"
        else
            echo \"⚠️  No environment backup found\"
        fi
        
        echo \"✅ Backup validation completed\"
    "
}

# Create pre-rollback backup
create_pre_rollback_backup() {
    log "Creating pre-rollback backup..."
    
    local timestamp=$(date +%Y%m%d_%H%M%S)
    
    ssh -i "$SSH_KEY_PATH" ubuntu@"$INSTANCE_IP" "
        BACKUP_DIR=\"/opt/fuzeinfra/backups/pre_rollback_$timestamp\"
        mkdir -p \$BACKUP_DIR
        
        # Backup current Docker volumes
        docker volume ls --filter 'name=fuzeinfra_' -q | while read volume; do
            echo \"Backing up current volume: \$volume\"
            docker run --rm -v \$volume:/data -v \$BACKUP_DIR:/backup alpine \
                tar czf /backup/\${volume}.tar.gz -C /data .
        done
        
        # Backup current configuration
        if [ -d '/opt/fuzeinfra/config' ]; then
            cp -r /opt/fuzeinfra/config \$BACKUP_DIR/
        fi
        
        if [ -f '/opt/fuzeinfra/.env' ]; then
            cp /opt/fuzeinfra/.env \$BACKUP_DIR/
        fi
        
        echo \"✅ Pre-rollback backup created: \$BACKUP_DIR\"
    "
    
    success "Pre-rollback backup created"
}

# Stop services for rollback
stop_services() {
    log "Stopping FuzeInfra services for rollback..."
    
    ssh -i "$SSH_KEY_PATH" ubuntu@"$INSTANCE_IP" "
        cd /opt/fuzeinfra
        
        echo 'Stopping all FuzeInfra containers...'
        docker-compose -f docker-compose.FuzeInfra.yml down
        
        echo 'Removing stopped containers...'
        docker ps -aq --filter 'name=fuzeinfra-*' | xargs -r docker rm
        
        echo '✅ Services stopped'
    "
    
    success "Services stopped successfully"
}

# Restore volumes
restore_volumes() {
    local backup_timestamp="$1"
    
    log "Restoring Docker volumes from backup: $backup_timestamp"
    
    ssh -i "$SSH_KEY_PATH" ubuntu@"$INSTANCE_IP" "
        BACKUP_DIR=\"/opt/fuzeinfra/backups/$backup_timestamp\"
        
        # Remove existing volumes
        echo 'Removing existing FuzeInfra volumes...'
        docker volume ls --filter 'name=fuzeinfra_' -q | xargs -r docker volume rm
        
        # Restore volumes from backup
        find \$BACKUP_DIR -name '*.tar.gz' | while read backup_file; do
            volume_name=\$(basename \"\$backup_file\" .tar.gz)
            echo \"Restoring volume: \$volume_name\"
            
            # Create volume
            docker volume create \$volume_name
            
            # Restore data
            docker run --rm -v \$volume_name:/data -v \$BACKUP_DIR:/backup alpine \
                tar xzf /backup/\${volume_name}.tar.gz -C /data
        done
        
        echo '✅ Volume restoration completed'
    "
    
    success "Volumes restored successfully"
}

# Restore configuration
restore_configuration() {
    local backup_timestamp="$1"
    
    log "Restoring configuration from backup: $backup_timestamp"
    
    ssh -i "$SSH_KEY_PATH" ubuntu@"$INSTANCE_IP" "
        BACKUP_DIR=\"/opt/fuzeinfra/backups/$backup_timestamp\"
        
        # Restore configuration directory
        if [ -d \"\$BACKUP_DIR/config\" ]; then
            echo 'Restoring configuration directory...'
            rm -rf /opt/fuzeinfra/config
            cp -r \$BACKUP_DIR/config /opt/fuzeinfra/
            echo '✅ Configuration directory restored'
        fi
        
        # Restore environment file
        if [ -f \"\$BACKUP_DIR/.env\" ]; then
            echo 'Restoring environment file...'
            cp \$BACKUP_DIR/.env /opt/fuzeinfra/
            echo '✅ Environment file restored'
        fi
    "
    
    success "Configuration restored successfully"
}

# Start services after rollback
start_services() {
    log "Starting FuzeInfra services after rollback..."
    
    ssh -i "$SSH_KEY_PATH" ubuntu@"$INSTANCE_IP" "
        cd /opt/fuzeinfra
        
        # Ensure network exists
        if ! docker network inspect FuzeInfra >/dev/null 2>&1; then
            echo 'Creating FuzeInfra network...'
            docker network create FuzeInfra
        fi
        
        # Start services
        echo 'Starting FuzeInfra services...'
        docker-compose -f docker-compose.FuzeInfra.yml up -d
        
        echo '✅ Services started'
    "
    
    success "Services started successfully"
}

# Verify rollback
verify_rollback() {
    log "Verifying rollback success..."
    
    # Wait for services to start
    sleep 30
    
    ssh -i "$SSH_KEY_PATH" ubuntu@"$INSTANCE_IP" "
        cd /opt/fuzeinfra
        
        echo '=== Container Status ==='
        docker-compose -f docker-compose.FuzeInfra.yml ps
        
        echo -e '\n=== Health Checks ==='
        
        # Check critical services
        SERVICES=('postgres:5432' 'mongodb:27017' 'redis:6379' 'nginx:80')
        FAILED_SERVICES=''
        
        for service_port in \"\${SERVICES[@]}\"; do
            service=\$(echo \$service_port | cut -d: -f1)
            port=\$(echo \$service_port | cut -d: -f2)
            
            if docker exec fuzeinfra-\$service sh -c 'exit 0' 2>/dev/null; then
                echo \"✅ \$service container is running\"
                
                if nc -z localhost \$port 2>/dev/null; then
                    echo \"✅ \$service port \$port is accessible\"
                else
                    echo \"❌ \$service port \$port is not accessible\"
                    FAILED_SERVICES=\"\$FAILED_SERVICES \$service\"
                fi
            else
                echo \"❌ \$service container is not running\"
                FAILED_SERVICES=\"\$FAILED_SERVICES \$service\"
            fi
        done
        
        if [ ! -z \"\$FAILED_SERVICES\" ]; then
            echo \"❌ Some services failed after rollback: \$FAILED_SERVICES\"
            exit 1
        fi
        
        echo '✅ All services are healthy after rollback'
    "
    
    success "Rollback verification completed"
}

# Generate rollback report
generate_rollback_report() {
    local backup_timestamp="$1"
    
    log "Generating rollback report..."
    
    ssh -i "$SSH_KEY_PATH" ubuntu@"$INSTANCE_IP" "
        cd /opt/fuzeinfra
        
        cat > rollback-report.json << EOF
{
  \"rollback_date\": \"\$(date -u +%Y-%m-%dT%H:%M:%SZ)\",
  \"backup_timestamp\": \"$backup_timestamp\",
  \"rolled_back_by\": \"\$(whoami)\",
  \"source_host\": \"\$(hostname)\",
  \"target_instance\": \"$INSTANCE_IP\",
  \"services_status\": {
    \"containers\": \$(docker ps --format '{{.Names}}' | wc -l),
    \"networks\": \$(docker network ls -q | wc -l),
    \"volumes\": \$(docker volume ls -q | wc -l)
  }
}
EOF
        
        echo 'Rollback report saved to rollback-report.json'
    "
    
    success "Rollback report generated"
}

# Main rollback function
main() {
    if [[ "${1:-}" == "--help" ]] || [[ "${1:-}" == "-h" ]]; then
        show_help
        exit 0
    fi
    
    validate_prerequisites
    
    if [[ -z "$BACKUP_TIMESTAMP" ]]; then
        list_backups
        echo ""
        echo "To perform rollback, specify a backup timestamp:"
        echo "Example: $0 20240129_143022"
        exit 0
    fi
    
    log "Starting FuzeInfra rollback to backup: $BACKUP_TIMESTAMP"
    log "Target instance: $INSTANCE_IP"
    
    # Confirmation prompt
    echo ""
    warn "⚠️  ROLLBACK OPERATION ⚠️"
    warn "This will restore FuzeInfra to backup: $BACKUP_TIMESTAMP"
    warn "Current data will be backed up but the current state will be lost."
    echo ""
    read -p "Are you sure you want to proceed? (yes/no): " -r
    echo ""
    
    if [[ ! $REPLY =~ ^[Yy][Ee][Ss]$ ]]; then
        log "Rollback cancelled by user"
        exit 0
    fi
    
    validate_backup "$BACKUP_TIMESTAMP"
    create_pre_rollback_backup
    stop_services
    restore_volumes "$BACKUP_TIMESTAMP"
    restore_configuration "$BACKUP_TIMESTAMP"
    start_services
    verify_rollback
    generate_rollback_report "$BACKUP_TIMESTAMP"
    
    success "🔄 FuzeInfra rollback completed successfully!"
    
    log "Services restored from backup: $BACKUP_TIMESTAMP"
    log "Access your services at:"
    log "- Main URL: https://infra.fuzefront.com"
    log "- Grafana: https://infra.fuzefront.com:3001"
    log "- Prometheus: https://infra.fuzefront.com:9090"
    log "- Airflow: https://infra.fuzefront.com:8082"
}

# Run main function with all arguments
main "$@"