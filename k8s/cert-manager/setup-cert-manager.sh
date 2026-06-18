#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# Install cert-manager into the current kube-context and configure the
# FuzeInfra ClusterIssuers, so every dependent repo gets TLS "for free" just by
# adding a cert-manager.io/cluster-issuer annotation + a tls: block to its
# Ingress. See docs/cert-management.md.
#
# Prereqs: kubectl (pointed at the target cluster), an ingress-nginx controller
#          for the ACME HTTP-01 solver (prod).
#
# Usage:
#   ./k8s/cert-manager/setup-cert-manager.sh local   # self-signed *.dev.local CA (kind/k3s dev)
#   ACME_EMAIL=you@example.com \
#     ./k8s/cert-manager/setup-cert-manager.sh prod  # Let's Encrypt staging + prod issuers
#   ./k8s/cert-manager/setup-cert-manager.sh none    # install cert-manager only, no issuers
#
# Override the pinned version with: CERT_MANAGER_VERSION=v1.16.2
# -----------------------------------------------------------------------------
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENVIRONMENT="${1:-local}"
# Pin cert-manager for reproducibility; bump deliberately.
CERT_MANAGER_VERSION="${CERT_MANAGER_VERSION:-v1.16.2}"

command -v kubectl >/dev/null || { echo "kubectl not found"; exit 1; }

echo "==> Installing cert-manager ${CERT_MANAGER_VERSION} (CRDs + controller)"
kubectl apply -f \
  "https://github.com/cert-manager/cert-manager/releases/download/${CERT_MANAGER_VERSION}/cert-manager.yaml"

echo "==> Waiting for cert-manager to become ready"
for deploy in cert-manager cert-manager-webhook cert-manager-cainjector; do
  kubectl -n cert-manager rollout status "deploy/${deploy}" --timeout=180s
done

# The webhook can briefly reject the first ClusterIssuer apply even after the
# Deployment is Available (endpoints still propagating). Retry a few times.
apply_with_retry() {
  local file="$1"
  local attempt
  for attempt in 1 2 3 4 5 6; do
    if kubectl apply -f "$file"; then
      return 0
    fi
    echo "    cert-manager webhook not ready yet, retrying in 5s ($attempt/6)..."
    sleep 5
  done
  echo "ERROR: failed to apply $file after several attempts" >&2
  return 1
}

case "$ENVIRONMENT" in
  local)
    echo "==> Configuring local CA ClusterIssuer (fuzeinfra-local-ca)"
    apply_with_retry "$SCRIPT_DIR/issuers/local-ca.yaml"

    echo "==> Waiting for the root CA certificate to be issued"
    kubectl -n cert-manager wait --for=condition=Ready certificate/fuzeinfra-local-ca \
      --timeout=120s || echo "    (CA still issuing — re-check with: kubectl -n cert-manager get certificate)"

    CA_OUT="$SCRIPT_DIR/fuzeinfra-local-ca.crt"
    if kubectl -n cert-manager get secret fuzeinfra-local-ca >/dev/null 2>&1; then
      kubectl -n cert-manager get secret fuzeinfra-local-ca \
        -o jsonpath='{.data.tls\.crt}' | base64 -d > "$CA_OUT"
      echo "==> Exported root CA to: $CA_OUT"
    fi

    cat <<EOF

==> Local CA ready. Any Ingress can now request a trusted *.dev.local cert with:

  metadata:
    annotations:
      cert-manager.io/cluster-issuer: fuzeinfra-local-ca
  spec:
    tls:
      - hosts: [myapp.dev.local]
        secretName: myapp-tls

ONE-TIME: trust the root CA so browsers/curl accept *.dev.local certs.
The CA cert was written to:
  $CA_OUT

  # Linux (Debian/Ubuntu):
  sudo cp "$CA_OUT" /usr/local/share/ca-certificates/fuzeinfra-local-ca.crt
  sudo update-ca-certificates

  # macOS:
  sudo security add-trusted-cert -d -r trustRoot \\
    -k /Library/Keychains/System.keychain "$CA_OUT"

  # Windows (PowerShell, admin):
  Import-Certificate -FilePath "$CA_OUT" \\
    -CertStoreLocation Cert:\\LocalMachine\\Root

Firefox uses its own store — import the CA under Settings > Privacy & Security >
Certificates > View Certificates > Authorities > Import.
EOF
    ;;

  prod)
    if [[ -z "${ACME_EMAIL:-}" ]]; then
      echo "ERROR: set ACME_EMAIL=you@example.com for Let's Encrypt registration." >&2
      exit 1
    fi
    echo "==> Configuring Let's Encrypt ClusterIssuers (email: ${ACME_EMAIL})"
    TMP="$(mktemp)"
    trap 'rm -f "$TMP"' EXIT
    sed "s/__ACME_EMAIL__/${ACME_EMAIL}/g" "$SCRIPT_DIR/issuers/letsencrypt.yaml" > "$TMP"
    apply_with_retry "$TMP"
    cat <<'EOF'

==> Let's Encrypt issuers installed: letsencrypt-staging, letsencrypt-prod.

Test with STAGING first (avoids hitting prod rate limits), then switch:

  metadata:
    annotations:
      cert-manager.io/cluster-issuer: letsencrypt-prod   # or letsencrypt-staging
  spec:
    tls:
      - hosts: [app.your-domain.com]
        secretName: app-tls

Requirements: the hostname's public DNS must point at this cluster's
ingress-nginx, and ports 80/443 must be reachable for the HTTP-01 challenge.
Watch issuance:  kubectl describe certificate <name>  /  kubectl get challenges -A
EOF
    ;;

  none)
    echo "==> cert-manager installed; no ClusterIssuers configured (env=none)."
    ;;

  *)
    echo "Unknown environment '$ENVIRONMENT' (use: local | prod | none)" >&2
    exit 1
    ;;
esac

echo "==> Done."
