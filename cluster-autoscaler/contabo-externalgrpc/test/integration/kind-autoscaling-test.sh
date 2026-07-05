#!/usr/bin/env bash
# =============================================================================
# kind-autoscaling-test.sh
#
# End-to-end integration test proving the Cluster Autoscaler <-> Contabo
# externalgrpc provider loop works, WITHOUT touching real Contabo. The
# provider is run in FAKE_CLOUD=1 mode (internal/contabo.MemClient, an
# in-memory instance list — see internal/contabo/memclient.go), so this test
# only needs a local kind cluster + the fuzeinfra Helm chart.
#
# Flow:
#   1. Create (or reuse) a single-node kind cluster.
#   2. Build the provider image in this repo and `kind load` it (no registry
#      push needed).
#   3. Helm-install the fuzeinfra chart with clusterAutoscaler.enabled=true
#      and the provider's FAKE_CLOUD secret wired in.
#   4. Apply a Deployment whose pods cannot fit on the existing node(s) and
#      assert the provider logs an `IncreaseSize` (via MemClient's
#      "[fake-cloud] Create" log line, which NodeGroupIncreaseSize triggers).
#   5. Scale the unschedulable Deployment back to 0, wait for CA to drain and
#      delete the now-unneeded elastic node, and assert the provider logs a
#      "[fake-cloud] Delete" line (NodeGroupDeleteNodes).
#
# Exit code is non-zero if ANY assertion fails.
#
# *** THIS SCRIPT MUST NOT BE RUN ON THIS DEV HOST. ***
# See README.md in this directory: the dev host's Docker Desktop uses a
# cgroup v1 engine and kind's control plane cannot bootstrap on it (both
# multi-node and single-node configs hang at "Starting control-plane").
# Run this in CI (a host-level `kind-host` runner, see
# .github/workflows/kind-validate.yml for the pattern used elsewhere in this
# repo) or on a WSL2 / cgroup-v2 Docker engine.
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROVIDER_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"
REPO_ROOT="$(cd "${PROVIDER_DIR}/../.." && pwd)"

KIND_CLUSTER_NAME="${KIND_CLUSTER_NAME:-ca-fake-test}"
NAMESPACE="${NAMESPACE:-fuzeinfra}"
RELEASE_NAME="${RELEASE_NAME:-fuzeinfra}"
PROVIDER_IMAGE="${PROVIDER_IMAGE:-fuzeinfra-contabo-ca-provider:fake-test}"
HELM_CHART="${HELM_CHART:-${REPO_ROOT}/helm/fuzeinfra}"

INCREASE_TIMEOUT="${INCREASE_TIMEOUT:-180}"   # seconds to wait for IncreaseSize
DELETE_TIMEOUT="${DELETE_TIMEOUT:-300}"        # seconds to wait for DeleteNodes (scale-down-unneeded-time)

step() { echo; echo "=== [$(date -u +%H:%M:%S)] $* ==="; }
fail() { echo "FAIL: $*" >&2; exit 1; }

provider_pod() {
  kubectl -n "${NAMESPACE}" get pods \
    -l "app.kubernetes.io/component=contabo-ca-provider" \
    -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true
}

provider_logs() {
  local pod
  pod="$(provider_pod)"
  if [ -z "${pod}" ]; then
    return 0
  fi
  kubectl -n "${NAMESPACE}" logs "${pod}" --tail=-1 2>/dev/null || true
}

wait_for_log_pattern() {
  # wait_for_log_pattern <pattern> <timeout_seconds> <description>
  local pattern="$1" timeout="$2" desc="$3"
  local waited=0
  step "Waiting up to ${timeout}s for provider log pattern: ${pattern} (${desc})"
  while [ "${waited}" -lt "${timeout}" ]; do
    if provider_logs | grep -qE "${pattern}"; then
      echo "OK: matched '${pattern}' after ${waited}s"
      return 0
    fi
    sleep 5
    waited=$((waited + 5))
  done
  fail "timed out after ${timeout}s waiting for provider log pattern: ${pattern} (${desc})"
}

KEEP_CLUSTER="${KEEP_CLUSTER:-0}"

cleanup() {
  if [ "${KEEP_CLUSTER}" = "1" ]; then
    echo "KEEP_CLUSTER=1 set — leaving kind cluster '${KIND_CLUSTER_NAME}' running for inspection."
    return 0
  fi
  step "Cleanup: deleting kind cluster '${KIND_CLUSTER_NAME}'"
  kind delete cluster --name "${KIND_CLUSTER_NAME}" >/dev/null 2>&1 || true
}
trap cleanup EXIT

step "0. Preconditions"
for tool in kind kubectl helm docker; do
  command -v "${tool}" >/dev/null 2>&1 || fail "required tool not found on PATH: ${tool}"
done

step "1. Create kind cluster (single node is sufficient for the CA control loop)"
if kind get clusters 2>/dev/null | grep -qx "${KIND_CLUSTER_NAME}"; then
  echo "kind cluster '${KIND_CLUSTER_NAME}' already exists, reusing"
else
  kind create cluster --name "${KIND_CLUSTER_NAME}"
fi
kubectl config use-context "kind-${KIND_CLUSTER_NAME}"

step "2. Build provider image (FAKE_CLOUD mode is a runtime env switch, not a build-time one) and load into kind"
docker build -t "${PROVIDER_IMAGE}" "${PROVIDER_DIR}"
kind load docker-image "${PROVIDER_IMAGE}" --name "${KIND_CLUSTER_NAME}"

step "3. Namespace + fake-cloud provider secret"
kubectl create namespace "${NAMESPACE}" --dry-run=client -o yaml | kubectl apply -f -

# The provider Deployment does `envFrom: secretRef: <existingSecret>`
# unconditionally (see helm/fuzeinfra/templates/autoscaler/provider-deployment.yaml).
# In FAKE_CLOUD mode, loadConfig() no longer requires the Contabo/K3S keys, so
# this secret only needs to carry FAKE_CLOUD=1. Placeholder values are still
# supplied for the keys the chart's existingSecret contract documents, purely
# so this test secret matches what a real deployment's secret shape would be.
kubectl -n "${NAMESPACE}" delete secret fuzeinfra-ca-provider --ignore-not-found
kubectl -n "${NAMESPACE}" create secret generic fuzeinfra-ca-provider \
  --from-literal=FAKE_CLOUD=1 \
  --from-literal=CONTABO_CLIENT_ID=unused \
  --from-literal=CONTABO_CLIENT_SECRET=unused \
  --from-literal=CONTABO_API_USER=unused \
  --from-literal=CONTABO_API_PASSWORD=unused \
  --from-literal=K3S_NODE_TOKEN=unused \
  --from-literal=SSH_KEY_ID=1

step "4. helm install/upgrade with clusterAutoscaler + provider (fake mode) enabled"
helm upgrade --install "${RELEASE_NAME}" "${HELM_CHART}" \
  -n "${NAMESPACE}" --create-namespace \
  -f "${HELM_CHART}/values-local.yaml" \
  --set clusterAutoscaler.enabled=true \
  --set clusterAutoscaler.nodeGroup.minSize=0 \
  --set clusterAutoscaler.nodeGroup.maxSize=2 \
  --set clusterAutoscaler.scaleDownUnneededTime=1m \
  --set clusterAutoscaler.scaleDownDelayAfterAdd=1m \
  --set "clusterAutoscaler.provider.image=${PROVIDER_IMAGE}" \
  --set clusterAutoscaler.provider.productId=fake-product \
  --set clusterAutoscaler.provider.imageId=fake-image \
  --set clusterAutoscaler.provider.region=EU \
  --set clusterAutoscaler.provider.k3sServerUrl=https://fake-k3s.invalid:6443 \
  --set clusterAutoscaler.provider.minSize=0 \
  --set clusterAutoscaler.provider.maxSize=2 \
  --wait --timeout=5m

step "4b. Wait for provider + cluster-autoscaler rollout"
kubectl -n "${NAMESPACE}" rollout status deployment/fuzeinfra-contabo-ca-provider --timeout=120s
kubectl -n "${NAMESPACE}" rollout status deployment/fuzeinfra-cluster-autoscaler --timeout=120s

step "5. Apply an unschedulable Deployment (huge CPU request no node can satisfy)"
cat <<'EOF' | kubectl -n "${NAMESPACE}" apply -f -
apiVersion: apps/v1
kind: Deployment
metadata:
  name: unschedulable-demo
  labels:
    app: unschedulable-demo
spec:
  replicas: 3
  selector:
    matchLabels:
      app: unschedulable-demo
  template:
    metadata:
      labels:
        app: unschedulable-demo
    spec:
      containers:
        - name: pause
          image: registry.k8s.io/pause:3.9
          resources:
            requests:
              cpu: "1000"
              memory: "1000Gi"
EOF

step "6. Assert provider logs an IncreaseSize call (fake-cloud Create)"
wait_for_log_pattern '\[fake-cloud\] Create ' "${INCREASE_TIMEOUT}" "NodeGroupIncreaseSize -> MemClient.Create"

step "7. Scale the unschedulable Deployment back down to free the elastic node"
kubectl -n "${NAMESPACE}" scale deployment/unschedulable-demo --replicas=0

step "8. Assert scale-down path: provider logs a Delete call (NodeGroupDeleteNodes -> fake-cloud Delete)"
wait_for_log_pattern '\[fake-cloud\] Delete id=' "${DELETE_TIMEOUT}" "NodeGroupDeleteNodes -> MemClient.Delete"

step "All assertions passed: IncreaseSize and DeleteNodes both observed against the fake cloud."
echo "PASS"
