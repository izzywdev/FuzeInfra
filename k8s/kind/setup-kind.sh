#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# Bring up a local FuzeInfra Kubernetes cluster on kind, install the
# ingress-nginx controller, and deploy the FuzeInfra Helm chart.
#
# Prereqs: docker, kind, kubectl, helm.
# Usage:   ./k8s/kind/setup-kind.sh
# -----------------------------------------------------------------------------
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
CLUSTER_NAME="fuzeinfra"
NAMESPACE="fuzeinfra"

command -v kind   >/dev/null || { echo "kind not found - https://kind.sigs.k8s.io"; exit 1; }
command -v kubectl>/dev/null || { echo "kubectl not found"; exit 1; }
command -v helm   >/dev/null || { echo "helm not found"; exit 1; }

echo "==> Creating kind cluster '$CLUSTER_NAME' (if missing)"
if ! kind get clusters | grep -qx "$CLUSTER_NAME"; then
  kind create cluster --config "$SCRIPT_DIR/kind-cluster.yaml"
else
  echo "    cluster already exists, reusing"
fi
kubectl cluster-info --context "kind-${CLUSTER_NAME}"

echo "==> Installing ingress-nginx (kind provider)"
kubectl apply -f https://raw.githubusercontent.com/kubernetes/ingress-nginx/main/deploy/static/provider/kind/deploy.yaml

echo "==> Waiting for ingress-nginx controller to be ready"
kubectl wait --namespace ingress-nginx \
  --for=condition=ready pod \
  --selector=app.kubernetes.io/component=controller \
  --timeout=180s

echo "==> Deploying FuzeInfra Helm chart"
helm upgrade --install fuzeinfra "$REPO_ROOT/helm/fuzeinfra" \
  --namespace "$NAMESPACE" --create-namespace \
  -f "$REPO_ROOT/helm/fuzeinfra/values-local.yaml" \
  --wait --timeout 10m || {
    echo "Helm install reported a timeout; some heavy images (elasticsearch, kafka)"
    echo "may still be pulling. Check: kubectl -n $NAMESPACE get pods"
  }

cat <<EOF

==> Done.
Add these hostnames to your /etc/hosts (all -> 127.0.0.1) or use the dnsmasq
wildcard for *.dev.local:

  127.0.0.1 grafana.dev.local prometheus.dev.local airflow.dev.local \\
            flower.dev.local kafka-ui.dev.local mongo-express.dev.local \\
            rabbitmq.dev.local neo4j.dev.local alertmanager.dev.local

Then open:  http://grafana.dev.local  (admin / admin)
Check pods: kubectl -n $NAMESPACE get pods
EOF
