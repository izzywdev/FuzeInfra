#!/usr/bin/env bash
# setup-cloudflare-tunnel.sh
#
# Wires the Cloudflare Named Tunnel token into an existing k3s cluster.
# Run this after `terraform apply` in terraform/cloudflare/.
#
# Usage:
#   cd terraform/cloudflare
#   terraform apply
#   TOKEN=$(terraform output -raw tunnel_token)
#   cd ../../
#   KUBECONFIG=terraform/contabo/k3s-kubeconfig.yaml \
#     scripts/setup-cloudflare-tunnel.sh "$TOKEN"
#
# Or provide the token via env var:
#   CLOUDFLARE_TUNNEL_TOKEN=<token> scripts/setup-cloudflare-tunnel.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# ---------------------------------------------------------------------------
# Resolve token
# ---------------------------------------------------------------------------
TOKEN="${1:-${CLOUDFLARE_TUNNEL_TOKEN:-}}"
if [[ -z "$TOKEN" ]]; then
  echo "ERROR: Tunnel token not provided."
  echo ""
  echo "Usage:"
  echo "  $0 <token>"
  echo "  CLOUDFLARE_TUNNEL_TOKEN=<token> $0"
  echo ""
  echo "Get the token from: cd terraform/cloudflare && terraform output -raw tunnel_token"
  exit 1
fi

# ---------------------------------------------------------------------------
# Resolve kubeconfig
# ---------------------------------------------------------------------------
KUBECONFIG="${KUBECONFIG:-$REPO_ROOT/terraform/contabo/k3s-kubeconfig.yaml}"
if [[ ! -f "$KUBECONFIG" ]]; then
  echo "ERROR: kubeconfig not found at $KUBECONFIG"
  echo "Set KUBECONFIG or run from the repo root after terraform/contabo has run."
  exit 1
fi
export KUBECONFIG

# ---------------------------------------------------------------------------
# Wait for the fuzeinfra-secrets Secret to exist (ArgoCD must have synced)
# ---------------------------------------------------------------------------
echo "Waiting for fuzeinfra-secrets to exist in the fuzeinfra namespace..."
for i in $(seq 1 30); do
  if kubectl get secret fuzeinfra-secrets -n fuzeinfra &>/dev/null; then
    echo "Secret found."
    break
  fi
  echo "  Not ready yet ($i/30), retrying in 10s..."
  sleep 10
done

if ! kubectl get secret fuzeinfra-secrets -n fuzeinfra &>/dev/null; then
  echo "ERROR: fuzeinfra-secrets not found after 5 minutes."
  echo "Make sure ArgoCD has synced the fuzeinfra Helm release first."
  exit 1
fi

# ---------------------------------------------------------------------------
# Patch the token into the secret
# ---------------------------------------------------------------------------
TOKEN_B64="$(printf '%s' "$TOKEN" | base64 -w0 2>/dev/null || printf '%s' "$TOKEN" | base64)"
kubectl patch secret fuzeinfra-secrets -n fuzeinfra \
  --type=merge \
  -p "{\"data\":{\"CLOUDFLARE_TUNNEL_TOKEN\":\"$TOKEN_B64\"}}"

echo ""
echo "Token stored. Restarting cloudflared to pick it up..."
kubectl rollout restart deployment/fuzeinfra-cloudflare-tunnel -n fuzeinfra 2>/dev/null || \
  echo "(cloudflared deployment not found — ArgoCD will start it on next sync)"

echo ""
echo "Apply the ArgoCD prod Ingress (argocd.prod.fuzefront.com):"
echo "  kubectl apply -f argocd/argocd-ingress-prod.yaml"
echo ""
echo "Update ArgoCD external URL for CORS:"
echo "  kubectl -n argocd patch configmap argocd-cm --type merge \\"
echo "    -p '{\"data\":{\"url\":\"https://argocd.prod.fuzefront.com\"}}'"
echo "  kubectl -n argocd rollout restart deployment/argocd-server"
echo ""
echo "Done. Monitor the tunnel:"
echo "  kubectl logs -n fuzeinfra -l app.kubernetes.io/name=cloudflare-tunnel -f"
