#!/usr/bin/env bash
# =============================================================================
# Bootstrap the ARC controller and staging runner scale set.
#
# Prerequisites:
#   - kubectl configured against the target cluster (kind fuzeinfra or EKS)
#   - helm >= 3.10
#   - The GitHub App / PAT secret created BEFORE this script (see github-secret.yaml)
#
# Usage:
#   ./runners/arc/install.sh            # install (idempotent)
#   ./runners/arc/install.sh --upgrade  # upgrade in place
#   ./runners/arc/install.sh --uninstall # remove everything
#
# The script does NOT register against GitHub — that happens automatically when
# the controller starts and reads the Secret.  The owner still needs to:
#   1. Pre-create the Secret (see github-secret.yaml)
#   2. Pre-create the org runner group "staging-runners" in GitHub UI
#   3. Add repos to the runner group allowlist
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

ARC_CONTROLLER_CHART="oci://ghcr.io/actions/actions-runner-controller-charts/gha-runner-scale-set-controller"
ARC_RUNNER_CHART="oci://ghcr.io/actions/actions-runner-controller-charts/gha-runner-scale-set"

CONTROLLER_NS="arc-systems"
RUNNER_NS="arc-runners"
CONTROLLER_RELEASE="arc-controller"
RUNNER_RELEASE="staging"        # becomes the runner label → runs-on: [self-hosted, staging]

UNINSTALL=false
UPGRADE=false
for arg in "$@"; do
  case "$arg" in
    --uninstall) UNINSTALL=true ;;
    --upgrade)   UPGRADE=true ;;
  esac
done

# ---- Uninstall --------------------------------------------------------------
if $UNINSTALL; then
  echo "==> Removing runner scale set …"
  helm uninstall "$RUNNER_RELEASE"    --namespace "$RUNNER_NS"    --ignore-not-found || true
  echo "==> Removing ARC controller …"
  helm uninstall "$CONTROLLER_RELEASE" --namespace "$CONTROLLER_NS" --ignore-not-found || true
  echo "Done. Namespaces preserved; remove manually if desired."
  exit 0
fi

# ---- Namespaces -------------------------------------------------------------
echo "==> Ensuring namespaces …"
kubectl create namespace "$CONTROLLER_NS" --dry-run=client -o yaml | kubectl apply -f -
kubectl create namespace "$RUNNER_NS"     --dry-run=client -o yaml | kubectl apply -f -

# ---- Runner ServiceAccount (arc-runner-sa in arc-runners) -------------------
echo "==> Applying runner ServiceAccount …"
kubectl apply -f - <<'EOF'
apiVersion: v1
kind: ServiceAccount
metadata:
  name: arc-runner-sa
  namespace: arc-runners
  labels:
    app.kubernetes.io/component: runner
    app.kubernetes.io/part-of: arc-staging
EOF

# ---- ARC controller ---------------------------------------------------------
if $UPGRADE; then
  echo "==> Upgrading ARC controller …"
  helm upgrade "$CONTROLLER_RELEASE" "$ARC_CONTROLLER_CHART" \
    --namespace "$CONTROLLER_NS" \
    --values "$SCRIPT_DIR/controller-values.yaml" \
    --wait
else
  echo "==> Installing ARC controller …"
  helm install "$CONTROLLER_RELEASE" "$ARC_CONTROLLER_CHART" \
    --namespace "$CONTROLLER_NS" \
    --values "$SCRIPT_DIR/controller-values.yaml" \
    --wait
fi

# ---- Runner scale set -------------------------------------------------------
if $UPGRADE; then
  echo "==> Upgrading runner scale set ($RUNNER_RELEASE) …"
  helm upgrade "$RUNNER_RELEASE" "$ARC_RUNNER_CHART" \
    --namespace "$RUNNER_NS" \
    --values "$SCRIPT_DIR/runner-scale-set-values.yaml" \
    --wait
else
  echo "==> Installing runner scale set ($RUNNER_RELEASE) …"
  helm install "$RUNNER_RELEASE" "$ARC_RUNNER_CHART" \
    --namespace "$RUNNER_NS" \
    --values "$SCRIPT_DIR/runner-scale-set-values.yaml" \
    --wait
fi

echo ""
echo "✓ ARC controller running in namespace: $CONTROLLER_NS"
echo "✓ Runner scale set '$RUNNER_RELEASE' registered in namespace: $RUNNER_NS"
echo ""
echo "Next steps:"
echo "  1. Confirm the runner appears in GitHub:"
echo "     https://github.com/organizations/izzywdev/settings/actions/runners"
echo "  2. Assign the runner to the 'staging-runners' group with your repo allowlist."
echo "  3. For each consumer app: apply the deployer RBAC (see runners/rbac/)."
