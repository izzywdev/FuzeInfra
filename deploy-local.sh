#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# deploy-local.sh — deploy FuzeInfra to the local kind cluster
#
# Usage:
#   ./deploy-local.sh            # install or upgrade
#   ./deploy-local.sh --dry-run  # preview rendered manifests without applying
#   ./deploy-local.sh --uninstall
#
# Prerequisites:
#   - kind cluster running (k8s/kind/setup-kind.sh)
#   - kubectl context pointing at the kind cluster
#   - helm >= 3.10
# -----------------------------------------------------------------------------
set -euo pipefail

RELEASE="fuzeinfra"
NAMESPACE="fuzeinfra"
CHART="helm/fuzeinfra"
VALUES="helm/fuzeinfra/values-local.yaml"
TIMEOUT="10m"

DRY_RUN=false
UNINSTALL=false

for arg in "$@"; do
  case "$arg" in
    --dry-run)   DRY_RUN=true ;;
    --uninstall) UNINSTALL=true ;;
    *) echo "Unknown flag: $arg"; exit 1 ;;
  esac
done

# Verify kind cluster context
CONTEXT=$(kubectl config current-context 2>/dev/null || echo "")
if [[ "$CONTEXT" != kind-* ]]; then
  echo "ERROR: current kubectl context is '$CONTEXT', expected a kind-* context."
  echo "       Run: k8s/kind/setup-kind.sh  (or: kubectl config use-context kind-fuzeinfra)"
  exit 1
fi

if $UNINSTALL; then
  echo "Uninstalling $RELEASE from $NAMESPACE..."
  helm uninstall "$RELEASE" -n "$NAMESPACE" || true
  kubectl delete namespace "$NAMESPACE" --ignore-not-found
  echo "Done."
  exit 0
fi

echo "Deploying FuzeInfra to local kind cluster ($CONTEXT)..."
helm lint "$CHART" -f "$VALUES"

FLAGS=""
$DRY_RUN && FLAGS="--dry-run"

helm upgrade --install "$RELEASE" "$CHART" \
  --namespace "$NAMESPACE" \
  --create-namespace \
  -f "$VALUES" \
  --atomic \
  --timeout "$TIMEOUT" \
  $FLAGS

if ! $DRY_RUN; then
  echo ""
  echo "=== Pods ==="
  kubectl -n "$NAMESPACE" get pods
  echo ""
  echo "=== Services ==="
  kubectl -n "$NAMESPACE" get svc
  echo ""
  echo "Done. Access services at http://<service>.dev.local (add 127.0.0.1 to /etc/hosts or use dnsmasq)"
fi
