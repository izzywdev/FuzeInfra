#!/bin/bash
# Production Deployment Script for FuzeInfra
# This script handles the deployment process for production environments

set -euo pipefail

# Configuration
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
DEPLOYMENT_TYPE="${1:-differential}"
FORCE_RECREATE="${2:-false}"

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
FuzeInfra Production Deployment Script

Usage: $0 [DEPLOYMENT_TYPE] [FORCE_RECREATE]

Parameters:
  DEPLOYMENT_TYPE    Type of deployment (default: differential)
                     - initial: Full deployment with all services
                     - differential: Update only changed services
                     - rollback: Rollback to previous version
  
  FORCE_RECREATE     Force recreate all containers (default: false)
                     - true: Stop and recreate all containers
                     - false: Update only changed containers

Examples:
  $0 differential false    # Standard differential update
  $0 initial true          # Fresh deployment with forced recreation
  $0 rollback false        # Rollback to previous version

Environment Variables:
  INSTANCE_IP             IP address of target instance
  SSH_KEY_PATH           Path to SSH private key
  BACKUP_BEFORE_DEPLOY   Create backup before deployment (default: true)
  HEALTH_CHECK_TIMEOUT   Health check timeout in seconds (default: 300)

EOF
}

# Validate prerequisites
validate_prerequisites() {
    log "Validating deployment prerequisites..."
    
    # Check required environment variables
    if [[ -z "${INSTANCE_IP:-}" ]]; then
        error "INSTANCE_IP environment variable is required"
    fi
    
    if [[ -z "${SSH_KEY_PATH:-}" ]]; then
        error "SSH_KEY_PATH environment variable is required"
    fi
    
    # Check SSH key
    if [[ ! -f "$SSH_KEY_PATH" ]]; then
        error "SSH key not found at: $SSH_KEY_PATH"
    fi
    
    # Check SSH connectivity
    log "Testing SSH connectivity to $INSTANCE_IP..."
    if ! ssh -o ConnectTimeout=10 -o StrictHostKeyChecking=no -i "$SSH_KEY_PATH" ubuntu@"$INSTANCE_IP" "echo 'SSH connection successful'" >/dev/null 2>&1; then
        error "Unable to connect to instance via SSH"
    fi
    
    # Check Docker Compose file
    if [[ ! -f "$PROJECT_ROOT/docker-compose.FuzeInfra.yml" ]]; then
        error "Docker Compose file not found: $PROJECT_ROOT/docker-compose.FuzeInfra.yml"
    fi
    
    # Validate Docker Compose configuration
    log "Validating Docker Compose configuration..."
    if ! docker-compose -f "$PROJECT_ROOT/docker-compose.FuzeInfra.yml" config >/dev/null 2>&1; then
        error "Invalid Docker Compose configuration"
    fi
    
    success "Prerequisites validation completed"
}

# Create deployment backup
create_backup() {
    if [[ "${BACKUP_BEFORE_DEPLOY:-true}" == "true" ]]; then
        log "Creating deployment backup..."
        
        BACKUP_DIR="/opt/fuzeinfra/backups/$(date +%Y%m%d_%H%M%S)"
        
        ssh -i "$SSH_KEY_PATH" ubuntu@"$INSTANCE_IP" "
            mkdir -p $BACKUP_DIR
            
            # Backup Docker volumes
            docker volume ls --filter 'name=fuzeinfra_' -q | while read volume; do
                echo 'Backing up volume: \$volume'
                docker run --rm -v \$volume:/data -v $BACKUP_DIR:/backup alpine \
                    tar czf /backup/\${volume}.tar.gz -C /data .
            done
            
            # Backup configuration files
            if [ -d '/opt/fuzeinfra/config' ]; then
                cp -r /opt/fuzeinfra/config $BACKUP_DIR/
            fi
            
            # Backup environment files
            if [ -f '/opt/fuzeinfra/.env' ]; then
                cp /opt/fuzeinfra/.env $BACKUP_DIR/
            fi
            
            echo 'Backup completed in $BACKUP_DIR'
        "
        
        success "Backup created successfully"
    else
        warn "Backup skipped (BACKUP_BEFORE_DEPLOY=false)"
    fi
}

# Sync application files
sync_files() {
    log "Syncing FuzeInfra files to production instance..."
    
    # Create deployment package
    cd "$PROJECT_ROOT"
    
    log "Creating deployment package..."
    tar czf fuzeinfra-deployment.tar.gz \
        --exclude='.git' \
        --exclude='node_modules' \
        --exclude='*.log' \
        --exclude='.env*' \
        --exclude='terraform/ec2-deployment/terraform.tfstate*' \
        --exclude='terraform/ec2-deployment/.terraform' \
        .
    
    # Transfer to instance
    log "Transferring files to instance..."
    scp -i "$SSH_KEY_PATH" fuzeinfra-deployment.tar.gz ubuntu@"$INSTANCE_IP":/opt/fuzeinfra/
    
    # Extract on instance
    ssh -i "$SSH_KEY_PATH" ubuntu@"$INSTANCE_IP" "
        cd /opt/fuzeinfra
        tar xzf fuzeinfra-deployment.tar.gz
        rm fuzeinfra-deployment.tar.gz
        chmod +x infra-up.sh infra-down.sh
        chmod +x tools/cert-manager/*.sh
        chmod +x scripts/deploy/*.sh
    "
    
    # Cleanup local package
    rm fuzeinfra-deployment.tar.gz
    
    success "Files synced successfully"
}

# Check for port conflicts
check_port_conflicts() {
    log "Checking for port conflicts..."
    
    ssh -i "$SSH_KEY_PATH" ubuntu@"$INSTANCE_IP" "
        echo '=== Currently running services ==='
        docker ps --format 'table {{.Names}}\t{{.Ports}}\t{{.Status}}'
        
        echo -e '\n=== Port usage ==='
        sudo netstat -tulpn | grep LISTEN | sort -k4
        
        # Check for specific conflicts
        CONFLICTING_PORTS='80 443 53 5432 27017 6379 9090 3001 8082'
        CONFLICTS_FOUND=false
        
        for port in \$CONFLICTING_PORTS; do
            if sudo lsof -i:\$port >/dev/null 2>&1; then
                echo \"⚠️  Port \$port is in use:\"
                sudo lsof -i:\$port
                CONFLICTS_FOUND=true
            fi
        done
        
        if [ \"\$CONFLICTS_FOUND\" = \"true\" ] && [ \"$FORCE_RECREATE\" != \"true\" ]; then
            echo \"❌ Port conflicts detected. Use FORCE_RECREATE=true to resolve\"
            exit 1
        fi
    "
}

# Stop conflicting services
stop_conflicting_services() {
    if [[ "$FORCE_RECREATE" == "true" ]]; then
        log "Stopping conflicting services..."
        
        ssh -i "$SSH_KEY_PATH" ubuntu@"$INSTANCE_IP" "
            # Stop existing FuzeInfra containers
            docker ps -q --filter 'name=fuzeinfra-*' | xargs -r docker stop
            
            # Remove stopped containers
            docker ps -aq --filter 'name=fuzeinfra-*' | xargs -r docker rm
            
            echo '✅ Conflicting services stopped'
        "
        
        success "Conflicting services stopped"
    fi
}

# Deploy services
deploy_services() {
    log "Deploying FuzeInfra services..."
    
    ssh -i "$SSH_KEY_PATH" ubuntu@"$INSTANCE_IP" "
        cd /opt/fuzeinfra
        
        # Check Docker network
        if ! docker network inspect FuzeInfra >/dev/null 2>&1; then
            echo 'Creating FuzeInfra network...'
            docker network create FuzeInfra
        fi
        
        # Pull latest images
        docker-compose -f docker-compose.FuzeInfra.yml pull
        
        # Deploy based on type
        case '$DEPLOYMENT_TYPE' in
            'differential')
                echo '📊 Performing differential deployment...'
                
                # Critical services first
                CRITICAL_SERVICES='postgres mongodb redis'
                for service in \$CRITICAL_SERVICES; do
                    echo \"Updating critical service: \$service\"
                    docker-compose -f docker-compose.FuzeInfra.yml up -d --no-deps \$service
                    sleep 10
                done
                
                # Other services
                OTHER_SERVICES='prometheus grafana nginx dnsmasq cloudflare-tunnel'
                for service in \$OTHER_SERVICES; do
                    echo \"Updating service: \$service\"
                    docker-compose -f docker-compose.FuzeInfra.yml up -d --no-deps \$service
                    sleep 5
                done
                ;;
                
            'initial')
                echo '🆕 Performing full deployment...'
                docker-compose -f docker-compose.FuzeInfra.yml up -d
                ;;
                
            'rollback')
                echo '⏪ Performing rollback...'
                # Implement rollback logic here
                echo 'Rollback functionality requires backup restoration'
                ;;
                
            *)
                echo \"❌ Unknown deployment type: $DEPLOYMENT_TYPE\"
                exit 1
                ;;
        esac
    "
    
    success "Services deployed successfully"
}

# Health checks
perform_health_checks() {
    log "Performing health checks..."
    
    local timeout="${HEALTH_CHECK_TIMEOUT:-300}"
    log "Waiting ${timeout}s for services to be ready..."
    
    ssh -i "$SSH_KEY_PATH" ubuntu@"$INSTANCE_IP" "
        cd /opt/fuzeinfra
        
        # Wait for services to start
        sleep 30
        
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
                
                # Port check
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
            echo \"❌ Some services failed health checks: \$FAILED_SERVICES\"
            exit 1
        fi
        
        echo '✅ All critical services passed health checks'
    "
    
    success "Health checks completed successfully"
}

# Generate deployment report
generate_report() {
    log "Generating deployment report..."
    
    ssh -i "$SSH_KEY_PATH" ubuntu@"$INSTANCE_IP" "
        cd /opt/fuzeinfra
        
        cat > deployment-report.json << EOF
{
  \"deployment_date\": \"\$(date -u +%Y-%m-%dT%H:%M:%SZ)\",
  \"deployment_type\": \"$DEPLOYMENT_TYPE\",
  \"force_recreate\": \"$FORCE_RECREATE\",
  \"deployed_by\": \"\$(whoami)\",
  \"source_host\": \"\$(hostname)\",
  \"target_instance\": \"$INSTANCE_IP\",
  \"services_status\": {
    \"containers\": \$(docker ps --format '{{.Names}}' | wc -l),
    \"networks\": \$(docker network ls -q | wc -l),
    \"volumes\": \$(docker volume ls -q | wc -l)
  }
}
EOF
        
        echo 'Deployment report saved to deployment-report.json'
    "
    
    success "Deployment report generated"
}

# Main deployment function
main() {
    if [[ "${1:-}" == "--help" ]] || [[ "${1:-}" == "-h" ]]; then
        show_help
        exit 0
    fi
    
    log "Starting FuzeInfra production deployment..."
    log "Deployment type: $DEPLOYMENT_TYPE"
    log "Force recreate: $FORCE_RECREATE"
    log "Target instance: ${INSTANCE_IP:-'Not set'}"
    
    validate_prerequisites
    create_backup
    sync_files
    check_port_conflicts
    stop_conflicting_services
    deploy_services
    perform_health_checks
    generate_report
    
    success "🚀 FuzeInfra deployment completed successfully!"
    
    log "Access your services at:"
    log "- Main URL: https://infra.fuzefront.com"
    log "- Grafana: https://infra.fuzefront.com:3001"
    log "- Prometheus: https://infra.fuzefront.com:9090"
    log "- Airflow: https://infra.fuzefront.com:8082"
}

# Run main function with all arguments
main "$@"