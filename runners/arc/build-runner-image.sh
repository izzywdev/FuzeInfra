#!/usr/bin/env bash
# =============================================================================
# build-runner-image.sh — build & push the FuzeInfra ARC runner image.
#
# The image is the official actions-runner + the Docker Compose V2 plugin
# (see runners/arc/Dockerfile and izzywdev/FuzeInfra#223).  Scale sets point at
# it via runners/arc/runner-scale-set-values.yaml and runners/arc/register-repo.sh.
#
# Usage:
#   ./runners/arc/build-runner-image.sh                 # build + push :latest
#   TAG=v1 ./runners/arc/build-runner-image.sh          # custom tag
#   PUSH=false ./runners/arc/build-runner-image.sh      # build only, no push
#   BASE_TAG=2.321.0 ./runners/arc/build-runner-image.sh # pin the base runner
#
# Env overrides:
#   IMAGE     target image repo   (default: ghcr.io/izzywdev/fuzeinfra-runner)
#   TAG       image tag           (default: latest)
#   BASE_TAG  base runner tag     (default: latest)
#   PUSH      push after build    (default: true)
#
# Push requires `docker login ghcr.io` (e.g. `echo $GH_TOKEN | docker login \
# ghcr.io -u <user> --password-stdin`) with a token that has write:packages.
# =============================================================================
set -euo pipefail

IMAGE="${IMAGE:-ghcr.io/izzywdev/fuzeinfra-runner}"
TAG="${TAG:-latest}"
BASE_TAG="${BASE_TAG:-latest}"
PUSH="${PUSH:-true}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "==> Building ${IMAGE}:${TAG} (base actions-runner:${BASE_TAG}) …"
docker build \
  --build-arg BASE_TAG="${BASE_TAG}" \
  -t "${IMAGE}:${TAG}" \
  "${SCRIPT_DIR}"

if [[ "${PUSH}" == "true" ]]; then
  echo "==> Pushing ${IMAGE}:${TAG} …"
  docker push "${IMAGE}:${TAG}"
  echo "✓ Pushed ${IMAGE}:${TAG}"
else
  echo "✓ Built ${IMAGE}:${TAG} (PUSH=false — not pushed)"
fi
