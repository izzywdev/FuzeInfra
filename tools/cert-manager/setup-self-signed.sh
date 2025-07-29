#!/bin/bash
# Self-signed Certificate Setup for FuzeInfra
# Creates basic self-signed certificates (will show browser warnings)

set -e

CERT_DIR="$(dirname "$0")/../../docker/certs"

echo "Creating self-signed certificates..."

mkdir -p "$CERT_DIR"
cd "$CERT_DIR"

# Create private key
openssl genrsa -out fuzeinfra-selfsigned.key 2048

# Create certificate signing request
cat > cert-config.conf << EOF
[req]
distinguished_name = req_distinguished_name
req_extensions = v3_req
prompt = no

[req_distinguished_name]
C = US
ST = Development
L = Local
O = FuzeInfra
CN = *.dev.local

[v3_req]
keyUsage = keyEncipherment, dataEncipherment
extendedKeyUsage = serverAuth
subjectAltName = @alt_names

[alt_names]
DNS.1 = *.dev.local
DNS.2 = localhost
DNS.3 = grafana.dev.local
DNS.4 = prometheus.dev.local
DNS.5 = airflow.dev.local
IP.1 = 127.0.0.1
IP.2 = ::1
EOF

# Create self-signed certificate
openssl req -new -x509 -key fuzeinfra-selfsigned.key \
    -out fuzeinfra-selfsigned.crt -days 365 \
    -config cert-config.conf \
    -extensions v3_req

# Set permissions
chmod 644 *.crt
chmod 600 *.key

echo "Self-signed certificates created!"
echo "⚠️  Note: Browsers will show security warnings"
echo "   Use mkcert for trusted local certificates instead"