#!/bin/bash
# Let's Encrypt Certificate Setup for FuzeInfra
# Use this if you have a real domain configured in Cloudflare

set -e

CERT_DIR="$(dirname "$0")/../../docker/certs"
DOMAIN="${CLOUDFLARE_TUNNEL_DOMAIN:-example.com}"

echo "Setting up Let's Encrypt certificates for domain: $DOMAIN"

# Install certbot if not present
if ! command -v certbot &> /dev/null; then
    echo "Installing certbot..."
    if [[ "$OSTYPE" == "linux-gnu"* ]]; then
        sudo apt-get update
        sudo apt-get install -y certbot python3-certbot-dns-cloudflare
    elif [[ "$OSTYPE" == "darwin"* ]]; then
        brew install certbot
        pip3 install certbot-dns-cloudflare
    fi
fi

# Create certificate directory
mkdir -p "$CERT_DIR"

# Create Cloudflare credentials file
cat > "$CERT_DIR/cloudflare.ini" << EOF
# Cloudflare API credentials for certbot
dns_cloudflare_email = ${CLOUDFLARE_EMAIL}
dns_cloudflare_api_key = ${CLOUDFLARE_API_KEY}
EOF

chmod 600 "$CERT_DIR/cloudflare.ini"

# Request wildcard certificate
certbot certonly \
    --dns-cloudflare \
    --dns-cloudflare-credentials "$CERT_DIR/cloudflare.ini" \
    --dns-cloudflare-propagation-seconds 60 \
    -d "$DOMAIN" \
    -d "*.$DOMAIN" \
    -d "*.webhook.$DOMAIN" \
    -d "*.auth.$DOMAIN" \
    --non-interactive \
    --agree-tos \
    --email "${CLOUDFLARE_EMAIL}" \
    --cert-path "$CERT_DIR/letsencrypt.crt" \
    --key-path "$CERT_DIR/letsencrypt.key"

echo "Let's Encrypt certificates created successfully!"
echo "Certificates will auto-renew via cron job"