#!/bin/bash

# Local Development Orchestrator - Project Deployment Script
# Usage: ./deploy-project.sh <project-name> [project-path]

set -e  # Exit on error

# Configuration
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
TOOLS_DIR="$PROJECT_ROOT/tools"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Helper functions
log_info() {
    echo -e "${BLUE}ℹ️  $1${NC}"
}

log_success() {
    echo -e "${GREEN}✅ $1${NC}"
}

log_warning() {
    echo -e "${YELLOW}⚠️  $1${NC}"
}

log_error() {
    echo -e "${RED}❌ $1${NC}"
}

check_requirements() {
    log_info "Checking requirements..."
    
    # Check if Python is available
    if ! command -v python3 &> /dev/null; then
        log_error "Python 3 is required but not installed"
        exit 1
    fi
    
    # Check if Docker is running
    if ! docker info &> /dev/null; then
        log_error "Docker is not running or not accessible"
        exit 1
    fi
    
    # Check if shared nginx network exists
    if ! docker network ls | grep -q "FuzeInfra"; then
        log_warning "FuzeInfra network not found, creating it..."
        docker network create FuzeInfra
    fi
    
    log_success "Requirements check passed"
}

validate_project() {
    local project_path="$1"
    local project_name="$2"
    
    log_info "Validating project configuration..."
    
    if [ ! -d "$project_path" ]; then
        log_error "Project directory does not exist: $project_path"
        exit 1
    fi
    
    if [ ! -f "$project_path/docker-compose.yml" ]; then
        log_error "No docker-compose.yml found in project directory"
        exit 1
    fi
    
    log_success "Project configuration will be inferred from docker-compose.yml"
}

allocate_ports() {
    local project_name="$1"
    local compose_file="$2"
    
    log_info "Analyzing project and allocating ports for $project_name..."
    
    local port_result
    port_result=$(python3 "$TOOLS_DIR/port-allocator/port-allocator.py" allocate "$project_name" --compose-file "$compose_file" 2>/dev/null)
    
    if [ $? -ne 0 ]; then
        log_error "Failed to allocate ports"
        exit 1
    fi
    
    local success
    success=$(echo "$port_result" | python3 -c "import sys, json; print(json.load(sys.stdin)['success'])")
    
    if [ "$success" != "True" ]; then
        log_error "Port allocation failed: $(echo "$port_result" | python3 -c "import sys, json; print(json.load(sys.stdin).get('error', 'Unknown error'))")"
        exit 1
    fi
    
    # Extract allocated ports and set environment variables
    local allocated_ports
    allocated_ports=$(echo "$port_result" | python3 -c "import sys, json; print(' '.join(map(str, json.load(sys.stdin)['allocated_ports'])))")
    
    # Export port variables to environment
    echo "$port_result" | python3 -c "
import sys, json, os
data = json.load(sys.stdin)
for var, port in data['port_mapping'].items():
    if port:
        os.environ[var] = str(port)
        print(f'export {var}={port}')
" | while read line; do
        eval "$line"
    done
    
    log_success "Allocated ports: $allocated_ports"
    echo "$port_result"
}

inject_environment() {
    local project_path="$1"
    local port_allocation="$2"
    
    log_info "Injecting environment variables..."
    
    local env_result
    env_result=$(python3 "$TOOLS_DIR/env-manager/env-injector.py" inject "$project_path" --ports "$port_allocation" 2>/dev/null)
    
    if [ $? -ne 0 ]; then
        log_error "Failed to inject environment variables"
        exit 1
    fi
    
    local success
    success=$(echo "$env_result" | python3 -c "import sys, json; print(json.load(sys.stdin)['success'])")
    
    if [ "$success" != "True" ]; then
        log_error "Environment injection failed: $(echo "$env_result" | python3 -c "import sys, json; print(json.load(sys.stdin).get('error', 'Unknown error'))")"
        exit 1
    fi
    
    log_success "Environment variables injected successfully"
    echo "$env_result"
}

generate_nginx_config() {
    local project_name="$1"
    
    log_info "Generating nginx configuration..."
    
    local nginx_result
    nginx_result=$(python3 "$TOOLS_DIR/nginx-generator/nginx-generator.py" generate \
        --project-name "$project_name" 2>/dev/null)
    
    if [ $? -ne 0 ]; then
        log_error "Failed to generate nginx configuration"
        exit 1
    fi
    
    local success
    success=$(echo "$nginx_result" | python3 -c "import sys, json; print(json.load(sys.stdin)['success'])")
    
    if [ "$success" != "True" ]; then
        log_error "Nginx config generation failed: $(echo "$nginx_result" | python3 -c "import sys, json; print(json.load(sys.stdin).get('error', 'Unknown error'))")"
        exit 1
    fi
    
    log_success "Nginx configuration generated successfully"
    echo "$nginx_result"
}

update_dns() {
    local project_name="$1"
    
    log_info "Updating DNS routing for $project_name.dev.local..."
    
    local dns_result
    dns_result=$(python3 "$TOOLS_DIR/dns-manager/dns-manager.py" add "$project_name" 2>/dev/null)
    
    if [ $? -ne 0 ]; then
        log_warning "Failed to update DNS routing (may require admin privileges)"
        log_info "You can manually add this entry to your hosts file:"
        log_info "127.0.0.1    $project_name.dev.local"
        return 0
    fi
    
    local success
    success=$(echo "$dns_result" | python3 -c "import sys, json; print(json.load(sys.stdin)['success'])")
    
    if [ "$success" != "True" ]; then
        log_warning "DNS update failed: $(echo "$dns_result" | python3 -c "import sys, json; print(json.load(sys.stdin).get('error', 'Unknown error'))")"
        log_info "You can manually add this entry to your hosts file:"
        log_info "127.0.0.1    $project_name.dev.local"
        return 0
    fi
    
    log_success "DNS routing updated successfully"
}

start_shared_nginx() {
    log_info "Starting shared nginx if not running..."
    
    if ! docker ps | grep -q "fuzeinfra-shared-nginx"; then
        log_info "Starting shared nginx container..."
        cd "$PROJECT_ROOT/infrastructure/shared-nginx"
        docker-compose up -d
        sleep 5  # Wait for nginx to start
        log_success "Shared nginx started"
    else
        log_info "Shared nginx already running"
    fi
}

reload_nginx() {
    log_info "Reloading nginx configuration..."
    
    local reload_result
    reload_result=$(python3 "$TOOLS_DIR/nginx-generator/nginx-generator.py" reload 2>/dev/null)
    
    if [ $? -ne 0 ]; then
        log_warning "Failed to reload nginx configuration"
        return 1
    fi
    
    local success
    success=$(echo "$reload_result" | python3 -c "import sys, json; print(json.load(sys.stdin)['success'])")
    
    if [ "$success" != "True" ]; then
        log_warning "Nginx reload failed: $(echo "$reload_result" | python3 -c "import sys, json; print(json.load(sys.stdin).get('error', 'Unknown error'))")"
        return 1
    fi
    
    log_success "Nginx configuration reloaded"
}

start_project() {
    local project_path="$1"
    local project_name="$2"
    
    log_info "Starting project containers..."
    
    cd "$project_path"
    
    # Set compose project name to avoid conflicts
    export COMPOSE_PROJECT_NAME="$project_name"
    
    # Start containers
    if docker-compose up -d; then
        log_success "Project containers started successfully"
    else
        log_error "Failed to start project containers"
        exit 1
    fi
}

health_check() {
    local project_name="$1"
    local project_path="$2"
    
    log_info "Performing health checks..."
    
    # Wait a moment for services to start
    sleep 10
    
    # Check if domain is accessible
    if curl -s -f "http://$project_name.dev.local/nginx-health" > /dev/null 2>&1; then
        log_success "Nginx health check passed"
    else
        log_warning "Nginx health check failed - may need more time to start"
    fi
    
    # Try to check project health endpoint if available
    if curl -s -f "http://$project_name.dev.local/health" > /dev/null 2>&1; then
        log_success "Project health check passed"
    else
        log_info "Project health endpoint not available or not ready yet"
    fi
}

cleanup_on_error() {
    local project_name="$1"
    log_error "Deployment failed, cleaning up..."
    
    # Remove DNS entry
    python3 "$TOOLS_DIR/dns-manager/dns-manager.py" remove "$project_name" 2>/dev/null || true
    
    # Remove nginx config
    python3 "$TOOLS_DIR/nginx-generator/nginx-generator.py" remove "$project_name" 2>/dev/null || true
    
    # Reload nginx
    python3 "$TOOLS_DIR/nginx-generator/nginx-generator.py" reload 2>/dev/null || true
}

main() {
    # Parse arguments
    if [ $# -lt 1 ]; then
        echo "Usage: $0 <project-name> [project-path]"
        echo "Example: $0 sportsbuck ../projects/sportsbuck"
        exit 1
    fi
    
    local project_name="$1"
    local project_path="${2:-$PROJECT_ROOT/projects/$project_name}"
    
    echo "🚀 Deploying $project_name..."
    echo "📁 Project path: $project_path"
    echo ""
    
    # Set up error cleanup
    trap "cleanup_on_error '$project_name'" ERR
    
    # Execute deployment steps
    check_requirements
    validate_project "$project_path" "$project_name"
    
    local port_allocation
    port_allocation=$(allocate_ports "$project_name" "$project_path/docker-compose.yml")
    
    inject_environment "$project_path" "$port_allocation"
    generate_nginx_config "$project_name"
    update_dns "$project_name"
    start_shared_nginx
    reload_nginx
    start_project "$project_path" "$project_name"
    health_check "$project_name" "$project_path"
    
    # Success summary
    echo ""
    log_success "🎉 Deployment completed successfully!"
    echo ""
    echo "📊 Deployment Summary:"
    echo "   Project Name: $project_name"
    echo "   Project Path: $project_path"
    echo "   Access URL: http://$project_name.dev.local"
    
    # Extract and display allocated ports
    local allocated_ports
    allocated_ports=$(echo "$port_allocation" | python3 -c "import sys, json; data=json.load(sys.stdin); print(', '.join([f'{k}: {v}' for k,v in data['port_mapping'].items() if v]))")
    echo "   Allocated Ports: $allocated_ports"
    
    echo ""
    echo "🌐 Your application is now accessible at:"
    echo "   http://$project_name.dev.local"
    echo ""
    echo "📋 Next steps:"
    echo "   • Access your application using the URL above"
    echo "   • Check logs: docker-compose -f $project_path/docker-compose.yml logs"
    echo "   • Stop project: cd $project_path && docker-compose down"
    echo ""
}

# Run main function
main "$@" 