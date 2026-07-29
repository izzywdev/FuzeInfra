#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# Bring up a local FuzeInfra Kubernetes cluster on kind, install the
# ingress-nginx controller, and deploy the FuzeInfra Helm chart.
#
# Prereqs: docker, kind, kubectl, helm.
# Usage:   ./k8s/kind/setup-kind.sh [--profile <name>] [--reuse] [--no-cluster]
#   --profile <name>  Layer helm/fuzeinfra/profiles/<name>.yaml on top of
#                     values-local.yaml (e.g. minimal, data-stores, full).
#   --reuse           Reuse an existing cluster (already the default; accepted
#                     for parity with the PowerShell/CI entrypoints).
#   --no-cluster      Skip kind cluster creation and deploy into the CURRENT
#                     kube-context instead. This is how the Docker Desktop
#                     backend reuses this script: the addon install (ingress-nginx,
#                     cert-manager, the local-CA ClusterIssuer) and the chart
#                     deploy are identical on every backend, so they must not be
#                     reimplemented per backend and allowed to drift.
#                     Does not require the `kind` binary.
#
# Prefer `python3 scripts-tools/devenv.py up` — it wraps this script, picks the
# backend, and then verifies the result. This script only brings things up.
# -----------------------------------------------------------------------------
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
CLUSTER_NAME="fuzeinfra"
NAMESPACE="fuzeinfra"
CERT_MANAGER_VERSION="v1.16.2"   # bump deliberately

PROFILE=""
NO_CLUSTER=0
while [ $# -gt 0 ]; do
  case "$1" in
    --profile)    PROFILE="${2:-}"; shift 2 ;;
    --profile=*)  PROFILE="${1#*=}"; shift ;;
    --reuse)      shift ;;   # default already reuses; accepted for parity
    --no-cluster) NO_CLUSTER=1; shift ;;
    *) echo "unknown arg: $1" >&2; exit 1 ;;
  esac
done

command -v kubectl>/dev/null || { echo "kubectl not found"; exit 1; }
command -v helm   >/dev/null || { echo "helm not found"; exit 1; }

if [ "$NO_CLUSTER" -eq 1 ]; then
  echo "==> Using the current kube-context (--no-cluster): $(kubectl config current-context)"
  kubectl cluster-info >/dev/null || {
    echo "current kube-context is not reachable — is the cluster running?" >&2; exit 1; }
else
  command -v kind >/dev/null || { echo "kind not found - https://kind.sigs.k8s.io"; exit 1; }
  echo "==> Creating kind cluster '$CLUSTER_NAME' (if missing)"
  if ! kind get clusters | grep -qx "$CLUSTER_NAME"; then
    kind create cluster --config "$SCRIPT_DIR/kind-cluster.yaml"
  elif kubectl cluster-info --context "kind-${CLUSTER_NAME}" >/dev/null 2>&1; then
    echo "    cluster already exists and is reachable, reusing"
  else
    # A leftover-but-dead cluster (e.g. after a Docker restart) would otherwise
    # wedge every kubectl/helm call below — recreate it instead of reusing.
    echo "    existing cluster is unreachable — recreating"
    kind delete cluster --name "$CLUSTER_NAME" || true
    kind create cluster --config "$SCRIPT_DIR/kind-cluster.yaml"
  fi
  kubectl cluster-info --context "kind-${CLUSTER_NAME}"
fi

# Provider variant matters. The `kind` manifest pins the controller to a node
# labelled ingress-ready=true (kind-cluster.yaml sets that label) and publishes
# via hostPort. Docker Desktop's node carries no such label, so the kind manifest
# would leave the controller Pending forever; its `cloud` manifest uses a
# LoadBalancer Service, which Docker Desktop resolves to localhost.
if [ "$NO_CLUSTER" -eq 1 ]; then
  INGRESS_PROVIDER="cloud"
else
  INGRESS_PROVIDER="kind"
fi
echo "==> Installing ingress-nginx ($INGRESS_PROVIDER provider)"
kubectl apply -f "https://raw.githubusercontent.com/kubernetes/ingress-nginx/main/deploy/static/provider/${INGRESS_PROVIDER}/deploy.yaml"

echo "==> Waiting for ingress-nginx controller to be ready"
kubectl wait --namespace ingress-nginx \
  --for=condition=ready pod \
  --selector=app.kubernetes.io/component=controller \
  --timeout=180s

echo "==> Installing cert-manager ($CERT_MANAGER_VERSION)"
kubectl apply -f "https://github.com/cert-manager/cert-manager/releases/download/${CERT_MANAGER_VERSION}/cert-manager.yaml"
echo "==> Waiting for cert-manager to be ready"
kubectl wait --namespace cert-manager \
  --for=condition=Available --timeout=180s deployment --all

echo "==> Installing the FuzeInfra local CA ClusterIssuer (shared local TLS)"
# Retry: the CRDs/webhook may take a moment to register after the deployments are Available.
for i in 1 2 3 4 5; do
  kubectl apply -f "$REPO_ROOT/k8s/local-tls/cluster-issuer.yaml" && break
  echo "    cert-manager webhook not ready yet, retrying ($i)..."; sleep 6
done
kubectl wait --for=condition=Ready --timeout=120s \
  clusterissuer/fuzeinfra-local-ca 2>/dev/null || \
  echo "    (CA ClusterIssuer still initializing — it'll be Ready shortly)"

echo "==> Deploying FuzeInfra Helm chart"
PROFILE_ARGS=()
if [ -n "$PROFILE" ]; then
  PFILE="$REPO_ROOT/helm/fuzeinfra/profiles/${PROFILE}.yaml"
  [ -f "$PFILE" ] || {
    echo "profile '$PROFILE' not found at $PFILE" >&2
    echo "available: $(ls "$REPO_ROOT/helm/fuzeinfra/profiles" 2>/dev/null | sed 's/\.yaml$//' | tr '\n' ' ')" >&2
    exit 1
  }
  echo "    applying profile overlay: $PROFILE"
  PROFILE_ARGS=(-f "$PFILE")
fi
# A slow image pull is tolerable; a broken chart is not. The previous version
# swallowed EVERY helm failure as "reported a timeout" and exited 0, so a hard
# install error (e.g. a SealedSecret rendering on a cluster with no Sealed
# Secrets CRD) was reported as a successful deploy — the caller then "validated"
# an empty namespace and passed. Distinguish the two: tolerate a genuine
# --wait timeout, fail on anything else.
set +e
helm_out="$(
  helm upgrade --install fuzeinfra "$REPO_ROOT/helm/fuzeinfra" \
    --namespace "$NAMESPACE" --create-namespace \
    -f "$REPO_ROOT/helm/fuzeinfra/values-local.yaml" \
    ${PROFILE_ARGS[@]+"${PROFILE_ARGS[@]}"} \
    --wait --timeout 10m 2>&1
)"
helm_rc=$?
set -e
printf '%s\n' "$helm_out"

if [ "$helm_rc" -ne 0 ]; then
  if printf '%s' "$helm_out" | grep -qiE 'timed out waiting|context deadline exceeded'; then
    echo ""
    echo "WARNING: helm --wait timed out. Heavy images (elasticsearch, kafka) may still"
    echo "be pulling; the release itself installed. Check: kubectl -n $NAMESPACE get pods"
  else
    echo "" >&2
    echo "ERROR: helm install FAILED (exit $helm_rc) — this is not a timeout." >&2
    echo "The release is not deployed; do not treat the environment as up." >&2
    exit "$helm_rc"
  fi
fi

cat <<EOF

==> Done.
Add these hostnames to your /etc/hosts (all -> 127.0.0.1) or use the dnsmasq
wildcard for *.dev.local:

  127.0.0.1 grafana.dev.local prometheus.dev.local airflow.dev.local \\
            flower.dev.local kafka-ui.dev.local mongo-express.dev.local \\
            rabbitmq.dev.local neo4j.dev.local alertmanager.dev.local

Then open:  http://grafana.dev.local  (admin / admin)
Check pods: kubectl -n $NAMESPACE get pods

==> Local HTTPS (shared cert management — issue #10)
cert-manager + the 'fuzeinfra-local-ca' ClusterIssuer are installed. Any app's
Ingress gets a trusted cert by adding:
    annotations: { cert-manager.io/cluster-issuer: fuzeinfra-local-ca }
    spec.tls: [{ hosts: [<host>.dev.local], secretName: <host>-tls }]
Trust the local CA ONCE so browsers/curl accept it (no -k needed):
    kubectl -n cert-manager get secret fuzeinfra-local-ca -o jsonpath='{.data.tls\\.crt}' | base64 -d > fuzeinfra-local-ca.crt
    # then import fuzeinfra-local-ca.crt into your OS/browser trust store
See docs/LOCAL_TLS.md for the full consumer guide.
EOF
