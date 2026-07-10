#!/usr/bin/env bash
# =============================================================================
# build-and-push-runner-image.sh
# -----------------------------------------------------------------------------
# Builds the FuzeInfra ARC runner image (runners/arc/Dockerfile) — stock
# actions-runner + docker compose/buildx plugins + warm Python/Node toolcache —
# and pushes it to GHCR. The scale set (runner-scale-set-values.yaml) pins the
# resulting digest/tag.
#
# Because izzywdev is a personal account, the package lives under the user
# namespace: ghcr.io/izzywdev/fuzeinfra-arc-runner
#
# Usage:
#   echo "$GHCR_TOKEN" | docker login ghcr.io -u izzywdev --password-stdin
#   runners/arc/build-and-push-runner-image.sh [TAG]
#
# TAG defaults to a UTC datestamp; :latest is always updated too.
# Requires: docker (with buildx), a GHCR login with packages:write, AND (for a
# brand-new package) a token permitted to CREATE the package — a plain GitHub
# App installation token cannot create one, so first publish via the build
# workflow (runners/arc/workflows-to-install/build-runner-image.yml) or a PAT.
# After the first publish make the GHCR package PUBLIC (or wire an
# imagePullSecret into arc-runners) or runner pods will ImagePullBackOff.
# =============================================================================
set -euo pipefail

IMAGE="${IMAGE:-ghcr.io/izzywdev/fuzeinfra-arc-runner}"
TAG="${1:-$(date -u +%Y-%m-%d)}"
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "Building $IMAGE:$TAG (+ :latest) from $DIR/Dockerfile"
docker buildx build \
  --file "$DIR/Dockerfile" \
  --tag "$IMAGE:$TAG" \
  --tag "$IMAGE:latest" \
  --push \
  "$DIR"

echo
echo "Pushed. Pin the scale set to this digest:"
docker buildx imagetools inspect "$IMAGE:$TAG" --format '{{.Manifest.Digest}}' 2>/dev/null \
  || docker inspect "$IMAGE:$TAG" --format '{{index .RepoDigests 0}}'
