#!/bin/bash
# Script to set up GitHub repository secrets from local .env file
# Requires GitHub CLI (gh) to be installed and authenticated

set -euo pipefail

# Colors for output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

log() {
    echo -e "${GREEN}[INFO] $1${NC}"
}

warn() {
    echo -e "${YELLOW}[WARN] $1${NC}"
}

error() {
    echo -e "${RED}[ERROR] $1${NC}"
    exit 1
}

# Check if gh CLI is installed and authenticated
if ! command -v gh &> /dev/null; then
    error "GitHub CLI (gh) is not installed. Please install it first: https://cli.github.com/"
fi

if ! gh auth status &> /dev/null; then
    error "GitHub CLI is not authenticated. Please run 'gh auth login' first."
fi

# Check if .env file exists
ENV_FILE=".env"
if [[ ! -f "$ENV_FILE" ]]; then
    error ".env file not found. Please ensure you're in the FuzeInfra directory with a .env file."
fi

log "Setting up GitHub repository secrets from .env file..."

# Function to set a secret from .env file
set_secret_from_env() {
    local env_var="$1"
    local secret_name="$2"
    
    local value=$(grep "^${env_var}=" "$ENV_FILE" | cut -d'=' -f2- | sed 's/^"//' | sed 's/"$//')
    
    if [[ -n "$value" && "$value" != "" ]]; then
        echo "$value" | gh secret set "$secret_name"
        log "✅ Set secret: $secret_name"
    else
        warn "⚠️  Skipped $secret_name (empty or not found in .env)"
    fi
}

# Database passwords
log "Setting database passwords..."
set_secret_from_env "POSTGRES_PASSWORD" "POSTGRES_PASSWORD"
set_secret_from_env "MONGO_INITDB_ROOT_PASSWORD" "MONGODB_PASSWORD"
set_secret_from_env "GRAFANA_ADMIN_PASSWORD" "GRAFANA_ADMIN_PASSWORD"

# Additional service passwords
log "Setting service passwords..."
set_secret_from_env "DNSMASQ_ADMIN_PASSWORD" "DNSMASQ_ADMIN_PASSWORD"
set_secret_from_env "RABBITMQ_DEFAULT_PASS" "RABBITMQ_PASSWORD"
set_secret_from_env "REDIS_PASSWORD" "REDIS_PASSWORD"

# Extract Neo4j password from NEO4J_AUTH (format: neo4j/password)
NEO4J_PASSWORD=$(grep "^NEO4J_AUTH=" "$ENV_FILE" | cut -d'=' -f2 | cut -d'/' -f2)
if [[ -n "$NEO4J_PASSWORD" ]]; then
    echo "$NEO4J_PASSWORD" | gh secret set "NEO4J_PASSWORD"
    log "✅ Set secret: NEO4J_PASSWORD"
fi

# Airflow secrets
log "Setting Airflow secrets..."
set_secret_from_env "AIRFLOW_ADMIN_PASSWORD" "AIRFLOW_ADMIN_PASSWORD"
set_secret_from_env "AIRFLOW_FERNET_KEY" "AIRFLOW_FERNET_KEY"

# Security keys
log "Setting security keys..."
set_secret_from_env "JWT_SECRET" "JWT_SECRET"
set_secret_from_env "ENCRYPTION_KEY" "ENCRYPTION_KEY"

# Cloudflare configuration (if available)
log "Setting Cloudflare configuration..."
set_secret_from_env "CLOUDFLARE_API_KEY" "CLOUDFLARE_API_KEY"
set_secret_from_env "CLOUDFLARE_TUNNEL_TOKEN" "CLOUDFLARE_TUNNEL_TOKEN"

# Extract Cloudflare email from .env if set
CLOUDFLARE_EMAIL=$(grep "^CLOUDFLARE_EMAIL=" "$ENV_FILE" 2>/dev/null | cut -d'=' -f2- || echo "")
if [[ -n "$CLOUDFLARE_EMAIL" && "$CLOUDFLARE_EMAIL" != "" ]]; then
    echo "$CLOUDFLARE_EMAIL" | gh secret set "CLOUDFLARE_EMAIL"
    log "✅ Set secret: CLOUDFLARE_EMAIL"
fi

# Extract Cloudflare tunnel ID from token (if possible)
TUNNEL_TOKEN=$(grep "^CLOUDFLARE_TUNNEL_TOKEN=" "$ENV_FILE" 2>/dev/null | cut -d'=' -f2- || echo "")
if [[ -n "$TUNNEL_TOKEN" && "$TUNNEL_TOKEN" != "" ]]; then
    # Try to extract tunnel ID from token (base64 decode and parse JSON)
    TUNNEL_ID=$(echo "$TUNNEL_TOKEN" | base64 -d 2>/dev/null | jq -r '.t' 2>/dev/null || echo "")
    if [[ -n "$TUNNEL_ID" && "$TUNNEL_ID" != "null" ]]; then
        echo "$TUNNEL_ID" | gh secret set "CLOUDFLARE_TUNNEL_ID"
        log "✅ Set secret: CLOUDFLARE_TUNNEL_ID (extracted from token)"
    else
        warn "⚠️  Could not extract CLOUDFLARE_TUNNEL_ID from token"
    fi
fi

# Webhook registry key
log "Setting webhook configuration..."
set_secret_from_env "WEBHOOK_REGISTRY_KEY" "WEBHOOK_REGISTRY_KEY"

# Generate GitHub webhook secret if not exists
WEBHOOK_SECRET=$(openssl rand -hex 32)
echo "$WEBHOOK_SECRET" | gh secret set "WEBHOOK_SECRET"
log "✅ Generated and set: WEBHOOK_SECRET"

# Set current GitHub token (if authenticated)
if gh auth status &> /dev/null; then
    gh auth token | gh secret set "GH_TOKEN"
    log "✅ Set secret: GH_TOKEN (using current authenticated token)"
else
    warn "⚠️  GH_TOKEN: GitHub CLI not authenticated. Run 'gh auth login' first."
fi

# Set AWS credentials from local configuration
log "Setting AWS credentials..."
if command -v aws &> /dev/null; then
    AWS_ACCESS_KEY=$(aws configure get aws_access_key_id 2>/dev/null)
    AWS_SECRET_KEY=$(aws configure get aws_secret_access_key 2>/dev/null)
    
    if [[ -n "$AWS_ACCESS_KEY" && "$AWS_ACCESS_KEY" != "" ]]; then
        echo "$AWS_ACCESS_KEY" | gh secret set "AWS_ACCESS_KEY_ID"
        log "✅ Set secret: AWS_ACCESS_KEY_ID"
    else
        warn "⚠️  AWS_ACCESS_KEY_ID not found in AWS CLI configuration"
    fi
    
    if [[ -n "$AWS_SECRET_KEY" && "$AWS_SECRET_KEY" != "" ]]; then
        echo "$AWS_SECRET_KEY" | gh secret set "AWS_SECRET_ACCESS_KEY"
        log "✅ Set secret: AWS_SECRET_ACCESS_KEY"
    else
        warn "⚠️  AWS_SECRET_ACCESS_KEY not found in AWS CLI configuration"
    fi
else
    warn "⚠️  AWS CLI not found. Install AWS CLI to auto-configure credentials."
fi

# Setup SSH key management for EC2 instances
log "Setting up SSH key management..."

# Create SSH key for deployment if it doesn't exist
SSH_KEY_NAME="fuzeinfra-deployment-$(date +%Y%m%d)"
SSH_KEY_PATH="keys/${SSH_KEY_NAME}.pem"

if [[ ! -f "$SSH_KEY_PATH" ]]; then
    log "Generating new SSH key pair for deployment..."
    
    # Generate SSH key pair
    ssh-keygen -t rsa -b 4096 -f "$SSH_KEY_PATH" -N "" -C "fuzeinfra-deployment-key"
    
    # Set proper permissions
    chmod 600 "$SSH_KEY_PATH"
    chmod 644 "${SSH_KEY_PATH}.pub"
    
    log "✅ Generated SSH key pair: $SSH_KEY_PATH"
else
    log "Using existing SSH key: $SSH_KEY_PATH"
fi

# Upload private key to GitHub secrets
if [[ -f "$SSH_KEY_PATH" ]]; then
    gh secret set "EC2_SSH_PRIVATE_KEY" < "$SSH_KEY_PATH"
    log "✅ Set secret: EC2_SSH_PRIVATE_KEY"
    
    # Create key inventory entry
    echo "$(date): SSH key $SSH_KEY_NAME uploaded to GitHub secrets" >> "keys/key-inventory.log"
else
    warn "⚠️  SSH key file not found: $SSH_KEY_PATH"
fi

# Atlassian configuration placeholders
warn "⚠️  ATLASSIAN secrets need to be set manually:"
warn "    gh secret set ATLASSIAN_EMAIL --body 'your-email@domain.com'"
warn "    gh secret set ATLASSIAN_API_TOKEN --body 'your-api-token'"
warn "    gh secret set ATLASSIAN_INSTANCE --body 'your-instance.atlassian.net'"


# Optional Slack webhook
warn "⚠️  Optional: Set Slack webhook URL for notifications:"
warn "    gh secret set SLACK_WEBHOOK_URL --body 'https://hooks.slack.com/services/...'"

log ""
log "🎉 GitHub secrets setup completed!"
log ""
log "📋 Summary of what was set up automatically:"
log "   ✅ Database passwords (PostgreSQL, MongoDB, Neo4j)"
log "   ✅ Service passwords (Grafana, RabbitMQ, Redis, DNSMasq)"
log "   ✅ Airflow configuration (password, fernet key)"
log "   ✅ Security keys (JWT, encryption)"
log "   ✅ Cloudflare configuration (API key, tunnel token/ID)"
log "   ✅ Webhook registry key"
log "   ✅ Generated GitHub webhook secret"
log "   ✅ AWS credentials from local CLI configuration"
log "   ✅ SSH key pair for EC2 deployment"
log ""
log "⚙️  Manual setup still required:"
log "   🔑 Atlassian credentials (optional)"
log "   🔑 Slack webhook URL (optional)"
log ""
log "You can view all secrets with: gh secret list"