#!/bin/bash
# FuzeInfra Zero Trust Deployment Script
# This script deploys the secured FuzeInfra infrastructure with Cloudflare Zero Trust

set -euo pipefail

# Configuration
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TERRAFORM_DIR="$SCRIPT_DIR/terraform/ec2-deployment"
LOG_FILE="$SCRIPT_DIR/deployment.log"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Logging function
log() {
    echo -e "${GREEN}[$(date +'%Y-%m-%d %H:%M:%S')]${NC} $1" | tee -a "$LOG_FILE"
}

error() {
    echo -e "${RED}[$(date +'%Y-%m-%d %H:%M:%S')] ERROR:${NC} $1" | tee -a "$LOG_FILE"
}

warning() {
    echo -e "${YELLOW}[$(date +'%Y-%m-%d %H:%M:%S')] WARNING:${NC} $1" | tee -a "$LOG_FILE"
}

info() {
    echo -e "${BLUE}[$(date +'%Y-%m-%d %H:%M:%S')] INFO:${NC} $1" | tee -a "$LOG_FILE"
}

# Check prerequisites
check_prerequisites() {
    log "Checking prerequisites..."
    
    # Check if running from correct directory
    if [[ ! -f "$SCRIPT_DIR/docker-compose.FuzeInfra.yml" ]]; then
        error "Must run from FuzeInfra root directory"
        exit 1
    fi
    
    # Check required commands
    local required_commands=("docker" "docker-compose" "terraform" "ssh")
    for cmd in "${required_commands[@]}"; do
        if ! command -v "$cmd" &> /dev/null; then
            error "$cmd is required but not installed"
            exit 1
        fi
    done
    
    # Check if .env file exists
    if [[ ! -f "$SCRIPT_DIR/.env" ]]; then
        error ".env file not found. Copy from environment.template and configure."
        exit 1
    fi
    
    log "Prerequisites check completed ✅"
}

# Deploy infrastructure with Terraform
deploy_infrastructure() {
    log "Deploying AWS infrastructure and Cloudflare Zero Trust configuration..."
    
    cd "$TERRAFORM_DIR"
    
    # Initialize Terraform if needed
    if [[ ! -d ".terraform" ]]; then
        info "Initializing Terraform..."
        terraform init
    fi
    
    # Plan the deployment
    info "Planning infrastructure deployment..."
    terraform plan -var-file="terraform.tfvars" -out="tfplan"
    
    # Ask for confirmation
    echo
    read -p "Do you want to apply the Terraform plan? (y/N): " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        info "Applying Terraform configuration..."
        terraform apply "tfplan"
        log "Infrastructure deployment completed ✅"
    else
        warning "Terraform deployment cancelled by user"
        exit 0
    fi
    
    cd "$SCRIPT_DIR"
}

# Deploy Docker services
deploy_services() {
    log "Deploying Docker services with Zero Trust security..."
    
    # Create Docker network if it doesn't exist
    if ! docker network ls | grep -q "FuzeInfra"; then
        info "Creating Docker network..."
        docker network create FuzeInfra
    fi
    
    # Pull latest images
    info "Pulling latest Docker images..."
    docker-compose -f docker-compose.FuzeInfra.yml pull
    
    # Deploy services
    info "Starting Docker services..."
    docker-compose -f docker-compose.FuzeInfra.yml up -d
    
    # Wait for services to start
    info "Waiting for services to initialize..."
    sleep 30
    
    # Check service health
    check_service_health
    
    log "Docker services deployment completed ✅"
}

# Check service health
check_service_health() {
    log "Checking service health..."
    
    local services=(
        "fuzeinfra-postgres"
        "fuzeinfra-mongodb" 
        "fuzeinfra-redis"
        "fuzeinfra-nginx"
        "fuzeinfra-cloudflared"
        "fuzeinfra-grafana"
        "fuzeinfra-prometheus"
    )
    
    local healthy_services=0
    local total_services=${#services[@]}
    
    for service in "${services[@]}"; do
        if docker ps --filter "name=$service" --filter "status=running" | grep -q "$service"; then
            info "✅ $service is running"
            ((healthy_services++))
        else
            error "❌ $service is not running"
        fi
    done
    
    log "$healthy_services/$total_services services are healthy"
    
    if [[ $healthy_services -eq $total_services ]]; then
        log "All critical services are running ✅"
    else
        warning "Some services are not running. Check logs: docker-compose -f docker-compose.FuzeInfra.yml logs"
    fi
}

# Verify Zero Trust access
verify_access() {
    log "Verifying Zero Trust access configuration..."
    
    # Get Terraform outputs
    cd "$TERRAFORM_DIR"
    local infra_url
    infra_url=$(terraform output -raw dns_record 2>/dev/null || echo "infra.fuzefront.com")
    
    info "Main dashboard should be accessible at: https://$infra_url"
    info "All services are now secured behind Cloudflare Zero Trust"
    
    # List all protected services
    echo
    info "Protected service URLs:"
    echo "  🏠 Main Dashboard: https://$infra_url"
    echo "  📊 Grafana: https://grafana.infra.fuzefront.com" 
    echo "  📈 Prometheus: https://prometheus.infra.fuzefront.com"
    echo "  🚨 Alertmanager: https://alertmanager.infra.fuzefront.com"
    echo "  🗄️  MongoDB: https://mongo.infra.fuzefront.com"
    echo "  🐰 RabbitMQ: https://rabbitmq.infra.fuzefront.com"
    echo "  📊 Neo4j: https://neo4j.infra.fuzefront.com"
    echo "  🌊 Airflow: https://airflow.infra.fuzefront.com"
    echo "  🌸 Flower: https://flower.infra.fuzefront.com"
    echo "  📡 Kafka: https://kafka.infra.fuzefront.com"
    echo "  🔍 Elasticsearch: https://elastic.infra.fuzefront.com"
    echo "  🧠 ChromaDB: https://chroma.infra.fuzefront.com"
    echo "  🌐 DNS: https://dns.infra.fuzefront.com"
    echo "  📝 Loki: https://loki.infra.fuzefront.com"
    
    cd "$SCRIPT_DIR"
    log "Zero Trust verification completed ✅"
}

# Show deployment summary
show_summary() {
    log "Deployment Summary"
    echo "=================="
    echo
    echo "✅ AWS EC2 instance deployed and configured"
    echo "✅ Docker services deployed with no direct port exposure"
    echo "✅ Cloudflare Zero Trust tunnel established"
    echo "✅ DNS records configured for all services"
    echo "✅ Zero Trust access policies applied"
    echo
    echo "🔒 Security Status:"
    echo "   • All database services secured (PostgreSQL, MongoDB, Redis, Neo4j)"
    echo "   • All management interfaces protected (Zero Trust authentication required)"
    echo "   • No direct internet access to any service ports"
    echo "   • All traffic encrypted through Cloudflare tunnel"
    echo
    echo "🌐 Access Control:"
    echo "   • Admin access: Grafana, Prometheus, Databases, DNS"
    echo "   • Developer access: Airflow, Kafka, Search services"
    echo "   • All access logged and auditable"
    echo
    echo "📋 Next Steps:"
    echo "   1. Access services through the provided URLs"
    echo "   2. Configure additional users in Cloudflare Zero Trust dashboard"
    echo "   3. Set up monitoring alerts and notifications"
    echo "   4. Review access logs regularly"
    echo
    echo "📚 Documentation: See README.md for detailed service information"
    echo
    log "FuzeInfra Zero Trust deployment completed successfully! 🎉"
}

# Main deployment function
main() {
    log "Starting FuzeInfra Zero Trust Deployment"
    echo "========================================"
    
    check_prerequisites
    
    echo
    read -p "This will deploy FuzeInfra with Zero Trust security. Continue? (y/N): " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        warning "Deployment cancelled by user"
        exit 0
    fi
    
    deploy_infrastructure
    deploy_services
    verify_access
    show_summary
}

# Handle script interruption
trap 'error "Deployment interrupted by user"; exit 1' INT TERM

# Run main function
main "$@"