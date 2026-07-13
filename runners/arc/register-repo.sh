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
#   # override the CI runner image or container mode (both have CI-capable
#   # defaults — the FuzeInfra runner image + dind — so onboarding stays a
#   # single invocation):
#   ./runners/arc/register-repo.sh \
#       --repo-url  https://github.com/izzywdev/FuzeFront \
#       --name      fuzefront --secret arc-runner-github-app \
#       --runner-image ghcr.io/izzywdev/fuzeinfra-arc-runner:2026-07-13 \
#       --container-mode dind
#
#   # uninstall a repo's scale set
#   ./runners/arc/register-repo.sh --name fuzefront --uninstall
#
# After registration, add to your repo's workflows:
#   jobs:
#     build:
#       runs-on: fuzefront   # matches --name
# =============================================================================

set -euo pipefail

# ---- defaults ---------------------------------------------------------------
RUNNER_NS="arc-systems-runners"
RUNNER_NS="arc-runners"          # must match controller watchSingleNamespace
CONTROLLER_NS="arc-systems"
CONTROLLER_SA="arc-controller-gha-rs-controller"
ARC_RUNNER_CHART="oci://ghcr.io/actions/actions-runner-controller-charts/gha-runner-scale-set"
ARC_VERSION="0.14.2"
# DinD sidecars are unbounded and share the CI node (fuzeinfra-ci-runner-1,
# 4 CPU / ~7.75 GB). Cap concurrency so parallel compose/build stacks don't OOM
# the node; raise per-repo with --max-runners once the CI pool is scaled out.
MAX_RUNNERS=3
MIN_RUNNERS=0
# Runner image. Defaults to the FuzeInfra CI-capable image (runners/arc/Dockerfile:
# stock actions-runner + docker compose-v2 & buildx plugins + jq/curl + warm
# Python/Node toolcache + Playwright browser deps) so every scale set comes up
# CI-capable from this one provisioning change. Override with --runner-image or
# RUNNER_IMAGE (e.g. the stock ghcr.io/actions/actions-runner:latest — but that
# lacks `docker compose`, so gate-* / build-test jobs will fail on it).
#
# PREREQUISITE: the default image must be published + PUBLIC (or an imagePullSecret
# wired into arc-runners) or runner pods ImagePullBackOff. Publish it via the
# build-runner-image workflow (runners/arc/workflows-to-install/build-runner-image.yml)
# or runners/arc/build-and-push-runner-image.sh before onboarding new repos.
RUNNER_IMAGE="${RUNNER_IMAGE:-ghcr.io/izzywdev/fuzeinfra-arc-runner:latest}"

# Container mode for the runner pods. "dind" (default) injects a privileged
# docker:dind sidecar + init-dind-externals initContainer and wires DOCKER_HOST,
# giving docker / docker compose / docker buildx / docker run self-hosted. Kept
# parameterizable (--container-mode) but dind is the CI-capable default.
CONTAINER_MODE="dind"

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
    --runner-image)      RUNNER_IMAGE="$2"; shift 2 ;;
    --container-mode)    CONTAINER_MODE="$2"; shift 2 ;;
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

# ---- Build per-repo values (in-memory, no temp file) ------------------------
VALUES=$(cat <<YAML
githubConfigUrl: "${REPO_URL}"
githubConfigSecret: ${K8S_SECRET_NAME}
runnerScaleSetName: ${SCALE_SET_NAME}

minRunners: ${MIN_RUNNERS}
maxRunners: ${MAX_RUNNERS}

template:
  spec:
    serviceAccountName: arc-runner-sa
    containers:
      - name: runner
        image: ${RUNNER_IMAGE}
        # Headroom for buildkit client work + Node-based actions (1Gi OOMs).
        # NOTE: compose/build workloads run in the dind sidecar's cgroup, not
        # here; the sidecar is unbounded, so node capacity is the real limit.
        resources:
          requests:
            cpu: 500m
            memory: 1Gi
          limits:
            cpu: "2"
            memory: 2Gi
        env:
          - name: DISABLE_RUNNER_UPDATE
            value: "1"
    nodeSelector:
      fuzeinfra.io/pool: ci
    tolerations:
      - key: fuzeinfra.io/ci
        operator: Exists
        effect: NoSchedule

# ---- Docker-in-Docker -------------------------------------------------------
# Gives every consumer scale set a real Docker daemon: the chart injects a
# privileged dind sidecar (docker:dind) + an init-dind-externals initContainer
# and wires DOCKER_HOST for the runner. This makes docker build / docker buildx
# build --push / docker run work self-hosted (no separate kind-host runner).
# COMPOSE CAVEAT: the stock actions-runner image ships the docker CLI + buildx
# but NOT the compose-v2 plugin, so "docker compose -f ..." still fails with
# "unknown shorthand flag: 'f' in -f" until RUNNER_IMAGE points at a compose-
# enabled image (see runners/arc/Dockerfile). dind alone does not add compose.
# NOTE: existing scale sets must be RE-REGISTERED (re-run arc-register.yml, or
# FuzeInfra arc-reinstall-scaleset.yml for the staging set) to pick this up --
# the dind sidecar only appears on pods created after this Helm values change.
containerMode:
  type: ${CONTAINER_MODE}

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
echo "    runner image : $RUNNER_IMAGE"
echo "    containerMode: $CONTAINER_MODE"
echo "$VALUES" | helm upgrade --install "$SCALE_SET_NAME" \
  "$ARC_RUNNER_CHART" \
  --version "$ARC_VERSION" \
  --namespace "$RUNNER_NS" \
  --create-namespace \
  --values - \
  --wait --timeout 3m

echo ""
echo "✓ Scale set '${SCALE_SET_NAME}' registered for ${REPO_URL}"
echo ""
echo "Add to your repo's workflows:"
echo "  jobs:"
echo "    build:"
echo "      runs-on: ${SCALE_SET_NAME}"
echo ""
echo "Verify in GitHub:"
echo "  ${REPO_URL}/settings/actions/runners"
