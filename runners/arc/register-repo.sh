#!/usr/bin/env bash
# =============================================================================
# register-repo.sh — register a GitHub repo as an ARC self-hosted runner
#
# Installs a gha-runner-scale-set Helm release on the shared FuzeInfra ARC
# controller.  The controller already runs in arc-systems; this script only
# adds a new AutoscalingRunnerSet pointing at your repo.
#
# Prerequisites
#   - kubectl pointing at the FuzeInfra cluster (kubeconfig set up)
#   - helm >= 3.10
#   - A GitHub App or PAT with the repo in scope (see --help)
#
# Usage
#   ./runners/arc/register-repo.sh \
#       --repo-url   https://github.com/izzywdev/FuzeFront \
#       --name       fuzefront \
#       --app-id     123456 \
#       --app-install-id  789012 \
#       --app-private-key /path/to/private-key.pem
#
#   # or: supply an existing k8s secret that already holds the GitHub App creds
#   ./runners/arc/register-repo.sh \
#       --repo-url  https://github.com/izzywdev/FuzeFront \
#       --name      fuzefront \
#       --secret    arc-runner-github-app   # existing secret in arc-runners
#
#   # uninstall a repo's scale set
#   ./runners/arc/register-repo.sh --name fuzefront --uninstall
#
# After registration, add to your repo's workflows:
#   jobs:
#     build:
#       runs-on: fuzefront   # matches --name
#
# Docker-in-Docker is enabled by default (docker / docker compose work in jobs).
# Pass --no-dind to register a hardened, Docker-less runner instead.
# A node-shared hostedtoolcache (/opt/hostedtoolcache on the CI node) is always
# mounted so setup-python / setup-node hit a warm cache across ephemeral pods.
# =============================================================================

set -euo pipefail

# ---- defaults ---------------------------------------------------------------
RUNNER_NS="arc-systems-runners"
RUNNER_NS="arc-runners"          # must match controller watchSingleNamespace
CONTROLLER_NS="arc-systems"
CONTROLLER_SA="arc-controller-gha-rs-controller"
ARC_RUNNER_CHART="oci://ghcr.io/actions/actions-runner-controller-charts/gha-runner-scale-set"
ARC_VERSION="0.14.2"
MAX_RUNNERS=5
MIN_RUNNERS=0
# DinD (docker-in-docker) is ON by default so `docker` / `docker compose` gates
# work out of the box.  Pass --no-dind for a hardened, Docker-less runner.
DIND=true

REPO_URL=""
SCALE_SET_NAME=""
GITHUB_SECRET_NAME=""
APP_ID=""
APP_INSTALL_ID=""
APP_PRIVATE_KEY_FILE=""
UNINSTALL=false

# ---- arg parsing ------------------------------------------------------------
usage() {
  grep '^#' "$0" | sed 's/^# \{0,1\}//' | sed -n '/Usage/,/^===/{ /^===/d; p }'
  exit 0
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --repo-url)          REPO_URL="$2"; shift 2 ;;
    --name)              SCALE_SET_NAME="$2"; shift 2 ;;
    --secret)            GITHUB_SECRET_NAME="$2"; shift 2 ;;
    --app-id)            APP_ID="$2"; shift 2 ;;
    --app-install-id)    APP_INSTALL_ID="$2"; shift 2 ;;
    --app-private-key)   APP_PRIVATE_KEY_FILE="$2"; shift 2 ;;
    --max-runners)       MAX_RUNNERS="$2"; shift 2 ;;
    --no-dind)           DIND=false; shift ;;
    --uninstall)         UNINSTALL=true; shift ;;
    --help|-h)           usage ;;
    *) echo "Unknown option: $1"; usage ;;
  esac
done

# ---- validate ---------------------------------------------------------------
if [[ -z "$SCALE_SET_NAME" ]]; then
  echo "ERROR: --name is required (e.g. --name fuzefront)" >&2
  exit 1
fi

if $UNINSTALL; then
  echo "==> Uninstalling scale set '$SCALE_SET_NAME' from $RUNNER_NS …"
  helm uninstall "$SCALE_SET_NAME" --namespace "$RUNNER_NS" --wait 2>/dev/null \
    || echo "(not installed — nothing to do)"
  SECRET_NAME="arc-runner-${SCALE_SET_NAME}-github-app"
  kubectl -n "$RUNNER_NS" delete secret "$SECRET_NAME" --ignore-not-found
  echo "Done."
  exit 0
fi

if [[ -z "$REPO_URL" ]]; then
  echo "ERROR: --repo-url is required (e.g. --repo-url https://github.com/izzywdev/FuzeFront)" >&2
  exit 1
fi

# ---- GitHub App secret ------------------------------------------------------
if [[ -n "$GITHUB_SECRET_NAME" ]]; then
  # Caller supplied an existing secret name — verify it exists
  if ! kubectl -n "$RUNNER_NS" get secret "$GITHUB_SECRET_NAME" &>/dev/null; then
    echo "ERROR: secret '$GITHUB_SECRET_NAME' not found in namespace $RUNNER_NS" >&2
    exit 1
  fi
  K8S_SECRET_NAME="$GITHUB_SECRET_NAME"
  echo "==> Using existing secret: $K8S_SECRET_NAME"
else
  # Create a new secret from GitHub App credentials
  if [[ -z "$APP_ID" || -z "$APP_INSTALL_ID" || -z "$APP_PRIVATE_KEY_FILE" ]]; then
    echo "ERROR: supply --secret <existing> OR all three of --app-id / --app-install-id / --app-private-key" >&2
    exit 1
  fi
  if [[ ! -f "$APP_PRIVATE_KEY_FILE" ]]; then
    echo "ERROR: private key file not found: $APP_PRIVATE_KEY_FILE" >&2
    exit 1
  fi

  K8S_SECRET_NAME="arc-runner-${SCALE_SET_NAME}-github-app"
  echo "==> Creating GitHub App secret '$K8S_SECRET_NAME' …"
  kubectl -n "$RUNNER_NS" create secret generic "$K8S_SECRET_NAME" \
    --from-literal=github_app_id="$APP_ID" \
    --from-literal=github_app_installation_id="$APP_INSTALL_ID" \
    --from-file=github_app_private_key="$APP_PRIVATE_KEY_FILE" \
    --dry-run=client -o yaml | kubectl apply -f -
fi

# ---- DinD (docker-in-docker) block ------------------------------------------
# containerMode.type=dind makes the chart inject a privileged `dind` sidecar and
# wire DOCKER_HOST, so `docker` / `docker compose` gates run with no extra setup.
# NOTE: the compose/build workload runs inside the (unbounded) dind sidecar, not
# the runner container — the runner limits below only size the non-docker steps.
CONTAINER_MODE=""
if $DIND; then
  CONTAINER_MODE=$(cat <<'YAML'

containerMode:
  type: dind
YAML
)
fi

# ---- Build per-repo values (in-memory, no temp file) ------------------------
# Shared hostedtoolcache: a hostPath on the dedicated CI node, warmed once and
# reused by every ephemeral pod.  Fixes "Version X was not found in the local
# cache" + api.github.com timeouts from setup-python/setup-node re-downloading
# on each fresh pod.  A root initContainer makes the dir writable by the runner
# user (uid 1001); the chart preserves user initContainers/volumes under dind.
VALUES=$(cat <<YAML
githubConfigUrl: "${REPO_URL}"
githubConfigSecret: ${K8S_SECRET_NAME}
runnerScaleSetName: ${SCALE_SET_NAME}

minRunners: ${MIN_RUNNERS}
maxRunners: ${MAX_RUNNERS}

template:
  spec:
    serviceAccountName: arc-runner-sa
    initContainers:
      - name: tool-cache-perms
        image: busybox:1.36
        command: ["sh", "-c", "mkdir -p /opt/hostedtoolcache && chmod 0777 /opt/hostedtoolcache"]
        securityContext:
          runAsUser: 0
        volumeMounts:
          - name: hostedtoolcache
            mountPath: /opt/hostedtoolcache
    containers:
      - name: runner
        image: ghcr.io/actions/actions-runner:latest
        resources:
          requests:
            cpu: 500m
            memory: 512Mi
          limits:
            cpu: "2"
            memory: 4Gi
        env:
          - name: DISABLE_RUNNER_UPDATE
            value: "1"
        volumeMounts:
          - name: hostedtoolcache
            mountPath: /opt/hostedtoolcache
    volumes:
      - name: hostedtoolcache
        hostPath:
          path: /opt/hostedtoolcache
          type: DirectoryOrCreate
    nodeSelector:
      fuzeinfra.io/pool: ci
    tolerations:
      - key: fuzeinfra.io/ci
        operator: Exists
        effect: NoSchedule
${CONTAINER_MODE}
controllerServiceAccount:
  namespace: ${CONTROLLER_NS}
  name: ${CONTROLLER_SA}
YAML
)

# ---- Ensure runner SA exists in the namespace (idempotent) ------------------
echo "==> Ensuring runner ServiceAccount in $RUNNER_NS …"
kubectl apply -f - <<EOF
apiVersion: v1
kind: ServiceAccount
metadata:
  name: arc-runner-sa
  namespace: ${RUNNER_NS}
EOF

# ---- Helm install/upgrade ---------------------------------------------------
echo "==> Registering scale set '$SCALE_SET_NAME' for $REPO_URL …"
echo "$VALUES" | helm upgrade --install "$SCALE_SET_NAME" \
  "$ARC_RUNNER_CHART" \
  --version "$ARC_VERSION" \
  --namespace "$RUNNER_NS" \
  --create-namespace \
  --values - \
  --wait --timeout 3m

echo ""
echo "✓ Scale set '${SCALE_SET_NAME}' registered for ${REPO_URL}"
if $DIND; then
  echo "  • Docker-in-Docker: ENABLED (docker / docker compose available in jobs)"
else
  echo "  • Docker-in-Docker: disabled (--no-dind)"
fi
echo "  • Tool cache: /opt/hostedtoolcache shared on the CI node (warm setup-python/node)"
echo ""
echo "Add to your repo's workflows:"
echo "  jobs:"
echo "    build:"
echo "      runs-on: ${SCALE_SET_NAME}"
echo ""
echo "Verify in GitHub:"
echo "  ${REPO_URL}/settings/actions/runners"
