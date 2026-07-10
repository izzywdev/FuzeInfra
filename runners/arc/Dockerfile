# =============================================================================
# FuzeInfra ARC runner image
# -----------------------------------------------------------------------------
# The stock `ghcr.io/actions/actions-runner` image ships the Docker CLI but
# NEITHER the Compose v2 NOR the Buildx CLI plugin (verified empirically against
# ghcr.io/actions/actions-runner:latest — both are absent). So any
# `docker compose ...` or `docker buildx ...` step on the ARC scale set fails
# immediately with:
#   "unknown shorthand flag: 'f' in -f"
# (docker treats `compose` as an unknown top-level flag).  DinD (containerMode:
# dind) provides the *daemon* via a sidecar but does not add these client
# plugins — they must live in the runner image.  See izzywdev/FuzeInfra #223,
# #230, #231, #233, #235.
#
# This image also pre-warms /opt/hostedtoolcache with Python 3.10/3.11 and
# Node 20 so `actions/setup-python` / `actions/setup-node` resolve from the
# local cache instead of hitting api.github.com / objects.githubusercontent.com
# from a cold pod (setup-python was timing out fetching the version manifest).
#
# Build + push:  runners/arc/build-and-push-runner-image.sh
# Consumed by:   runners/arc/runner-scale-set-values.yaml (template.spec image)
#                runners/arc/register-repo.sh (--runner-image / RUNNER_IMAGE)
# =============================================================================

# Pinned by digest for reproducibility / supply-chain hardening. The trailing
# tag comment tracks the human-readable ref. Base is Ubuntu 24.04.
FROM ghcr.io/actions/actions-runner@sha256:08c30b0a7105f64bddfc485d2487a22aa03932a791402393352fdf674bda2c29
# ^ ghcr.io/actions/actions-runner:latest as of 2026-07-10

USER root

# --- Docker CLI plugins: compose v2 + buildx ---------------------------------
# Installed from Docker's official apt repo so we track upstream releases and
# land the plugins at /usr/libexec/docker/cli-plugins (where the CLI looks).
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends ca-certificates curl gnupg jq; \
    install -m 0755 -d /etc/apt/keyrings; \
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
      | gpg --dearmor -o /etc/apt/keyrings/docker.gpg; \
    chmod a+r /etc/apt/keyrings/docker.gpg; \
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
      https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
      > /etc/apt/sources.list.d/docker.list; \
    apt-get update; \
    apt-get install -y --no-install-recommends docker-compose-plugin docker-buildx-plugin; \
    rm -rf /var/lib/apt/lists/*; \
    # sanity: the plugins must be discoverable by the docker CLI
    docker compose version; \
    docker buildx version

# --- Warm hostedtoolcache: Node 20 -------------------------------------------
# Node distribution tarballs are relocatable, so we drop Node 20 straight into
# the setup-node cache layout (/opt/hostedtoolcache/node/<ver>/x64) + .complete
# marker so actions/setup-node@v4 resolves offline.
ARG NODE_VERSION=20.18.1
RUN set -eux; \
    dir="/opt/hostedtoolcache/node/${NODE_VERSION}/x64"; \
    mkdir -p "$dir"; \
    curl -fsSL "https://nodejs.org/dist/v${NODE_VERSION}/node-v${NODE_VERSION}-linux-x64.tar.gz" \
      | tar -xz --strip-components=1 -C "$dir"; \
    touch "/opt/hostedtoolcache/node/${NODE_VERSION}/x64.complete"; \
    "$dir/bin/node" --version

# --- Warm hostedtoolcache: Python 3.10 + 3.11 --------------------------------
# Uses the actions/python-versions relocatable builds (the exact artifacts
# setup-python downloads) so the cache layout + rpath fixups are correct. The
# multi-line jq lives in a helper script (Dockerfile RUN can't hold it inline).
COPY warm-python-toolcache.sh /usr/local/bin/warm-python-toolcache.sh
RUN chmod +x /usr/local/bin/warm-python-toolcache.sh; \
    /usr/local/bin/warm-python-toolcache.sh 3.10 3.11

USER runner
