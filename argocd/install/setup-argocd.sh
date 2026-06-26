#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# Install Argo CD into the current kube-context and bootstrap the FuzeInfra
# GitOps Application (App points at helm/fuzeinfra in this repo).
#
# Prereqs: kubectl, helm not required (Argo renders Helm itself).
# Usage:
#   ./argocd/install/setup-argocd.sh local   # bootstrap fuzeinfra-local (kind)
#   ./argocd/install/setup-argocd.sh aws     # bootstrap fuzeinfra-aws  (EKS)
#   ./argocd/install/setup-argocd.sh none    # install Argo CD only, no app
# -----------------------------------------------------------------------------
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ARGOCD_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
ENVIRONMENT="${1:-local}"
# Pin Argo CD for reproducibility; bump deliberately.
ARGOCD_VERSION="${ARGOCD_VERSION:-v2.13.3}"

command -v kubectl >/dev/null || { echo "kubectl not found"; exit 1; }

echo "==> Installing Argo CD ${ARGOCD_VERSION} into namespace 'argocd'"
kubectl create namespace argocd --dry-run=client -o yaml | kubectl apply -f -
kubectl apply -n argocd \
  -f "https://raw.githubusercontent.com/argoproj/argo-cd/${ARGOCD_VERSION}/manifests/install.yaml"

echo "==> Waiting for Argo CD server to be ready"
kubectl -n argocd rollout status deploy/argocd-server --timeout=300s

echo "==> Applying AppProjects (FuzeInfra owns the destination/security boundary)"
kubectl apply -f "$ARGOCD_DIR/projects/fuzeinfra.yaml"
# Consumer projects are FuzeInfra-owned + restricted (cannot deploy into the
# fuzeinfra namespace). Add one per consumer repo.
kubectl apply -f "$ARGOCD_DIR/projects/fuzefront.yaml"

case "$ENVIRONMENT" in
  local)
    echo "==> Bootstrapping Application fuzeinfra-local"
    kubectl apply -f "$ARGOCD_DIR/applications/fuzeinfra-local.yaml"
    ;;
  aws)
    echo "==> Bootstrapping Application fuzeinfra-aws"
    kubectl apply -f "$ARGOCD_DIR/applications/fuzeinfra-aws.yaml"
    ;;
  none)
    echo "==> Skipping Application bootstrap (env=none)"
    ;;
  *)
    echo "Unknown environment '$ENVIRONMENT' (use: local | aws | none)"; exit 1
    ;;
esac

echo
echo "==> Argo CD admin password:"
kubectl -n argocd get secret argocd-initial-admin-secret \
  -o jsonpath="{.data.password}" 2>/dev/null | base64 -d || \
  echo "(secret not found - it may have been rotated/deleted after first login)"
echo
cat <<'EOF'

==> Open the Argo CD UI:
  kubectl -n argocd port-forward svc/argocd-server 8080:443
  then browse https://localhost:8080  (user: admin)

NOTE: if this repo is PRIVATE, register repo credentials so Argo CD can read it:
  argocd repo add https://github.com/izzywdev/FuzeInfra.git \
    --username <user> --password <token>
  (or create a repository Secret labeled argocd.argoproj.io/secret-type=repository)
EOF
