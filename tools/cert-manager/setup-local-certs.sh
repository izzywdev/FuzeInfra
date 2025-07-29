#!/bin/bash
# Local Certificate Setup for FuzeInfra
# Uses mkcert to create locally-trusted certificates

set -e

CERT_DIR="$(dirname "$0")/../../docker/certs"
DOMAINS_FILE="$(dirname "$0")/domains.txt"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${GREEN}Setting up local certificates for FuzeInfra...${NC}"

# Check if mkcert is installed
if ! command -v mkcert &> /dev/null; then
    echo -e "${YELLOW}mkcert not found. Installing...${NC}"
    
    # Detect OS and install mkcert
    if [[ "$OSTYPE" == "linux-gnu"* ]]; then
        # Linux - use curl to download
        curl -JLO "https://dl.filippo.io/mkcert/latest?for=linux/amd64"
        chmod +x mkcert-v*-linux-amd64
        sudo mv mkcert-v*-linux-amd64 /usr/local/bin/mkcert
    elif [[ "$OSTYPE" == "darwin"* ]]; then
        # macOS - use homebrew if available
        if command -v brew &> /dev/null; then
            brew install mkcert
        else
            echo -e "${RED}Please install mkcert manually: https://github.com/FiloSottile/mkcert${NC}"
            exit 1
        fi
    elif [[ "$OSTYPE" == "msys" ]] || [[ "$OSTYPE" == "cygwin" ]]; then
        # Windows - use chocolatey if available
        if command -v choco &> /dev/null; then
            choco install mkcert
        else
            echo -e "${RED}Please install mkcert manually: https://github.com/FiloSottile/mkcert${NC}"
            exit 1
        fi
    else
        echo -e "${RED}Unsupported OS. Please install mkcert manually: https://github.com/FiloSottile/mkcert${NC}"
        exit 1
    fi
fi

# Create certificate directory
mkdir -p "$CERT_DIR"

# Install local CA
echo -e "${GREEN}Installing local Certificate Authority...${NC}"
mkcert -install

# Define domains to create certificates for
DOMAINS=(
    "*.dev.local"
    "localhost"
    "127.0.0.1"
    "::1"
    "grafana.dev.local"
    "prometheus.dev.local"
    "airflow.dev.local"
    "mongo-express.dev.local"
    "rabbitmq.dev.local"
    "dnsmasq.dev.local"
)

# Add tunnel domains if configured
if [ -n "$CLOUDFLARE_TUNNEL_DOMAIN" ]; then
    DOMAINS+=(
        "*.webhook.$CLOUDFLARE_TUNNEL_DOMAIN"
        "*.auth.$CLOUDFLARE_TUNNEL_DOMAIN"
        "grafana.$CLOUDFLARE_TUNNEL_DOMAIN"
        "prometheus.$CLOUDFLARE_TUNNEL_DOMAIN"
    )
fi

# Create wildcard certificate
echo -e "${GREEN}Creating certificates for local domains...${NC}"
cd "$CERT_DIR"

# Create wildcard cert for .dev.local
mkcert -cert-file fuzeinfra-dev-local.crt -key-file fuzeinfra-dev-local.key \
    "*.dev.local" "localhost" "127.0.0.1" "::1"

# Create specific service certificates
mkcert -cert-file fuzeinfra-services.crt -key-file fuzeinfra-services.key \
    "${DOMAINS[@]}"

# Create nginx-compatible combined certificate
cat fuzeinfra-dev-local.crt > fuzeinfra-combined.crt
cat fuzeinfra-services.crt >> fuzeinfra-combined.crt

# Set proper permissions
chmod 644 *.crt
chmod 600 *.key

echo -e "${GREEN}Certificates created successfully!${NC}"
echo -e "${YELLOW}Certificate files:${NC}"
echo "  - fuzeinfra-dev-local.crt/key - Wildcard cert for *.dev.local"
echo "  - fuzeinfra-services.crt/key - Service-specific certificates" 
echo "  - fuzeinfra-combined.crt - Combined certificate for nginx"
echo ""
echo -e "${GREEN}Location: $CERT_DIR${NC}"
echo ""
echo -e "${YELLOW}Next steps:${NC}"
echo "1. Update nginx configuration to use certificates"
echo "2. Restart FuzeInfra services"
echo "3. Access services via HTTPS (e.g., https://grafana.dev.local)"