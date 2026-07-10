# =============================================================================
# FuzeInfra ARC runner image
#
# WHY: the stock ghcr.io/actions/actions-runner image ships the docker CLI and
# the buildx plugin, but NOT the compose-v2 plugin. Under containerMode: dind
# that means `docker build` / `docker buildx` work, but `docker compose -f …`
# fails with:  unknown shorthand flag: 'f' in -f
# (docker parses `compose` as an unknown top-level command). This image drops
# the compose plugin into the same cli-plugins dir the base image uses for
# buildx, so `docker compose` resolves. It also warms the hostedtoolcache so
# setup-python / setup-node don't have to fetch the version manifest on cold,
# egress-restricted pods (issue #235).
#
# BUILD & PUBLISH (one-time, then re-run to bump versions):
#   docker build -t ghcr.io/izzywdev/fuzeinfra-arc-runner:2.319.1 runners/arc
#   docker push  ghcr.io/izzywdev/fuzeinfra-arc-runner:2.319.1
#   # make the GHCR package PUBLIC (or add an imagePullSecret to arc-runners),
#   # else runner pods ImagePullBackOff on the cluster.
#
# ACTIVATE: point the scale-set values at this image, then re-register:
#   - runners/arc/runner-scale-set-values.yaml  -> template.spec.containers[0].image
#   - runners/arc/register-repo.sh              -> RUNNER_IMAGE default (or pass RUNNER_IMAGE=)
# then re-run each consumer's arc-register.yml (and arc-reinstall-scaleset.yml
# for FuzeInfra's own `staging` set).
#
# Pin to a real runner release tag/digest — do not float :latest for a prod
# runner (the runner agent is version-pinned via DISABLE_RUNNER_UPDATE=1).
# =============================================================================
ARG RUNNER_VERSION=2.319.1
FROM ghcr.io/actions/actions-runner:${RUNNER_VERSION}

# ---- Docker Compose v2 plugin ----------------------------------------------
# Installed into the base image's CLI plugins dir so `docker compose` resolves.
ARG COMPOSE_VERSION=v2.29.7
ARG TARGETARCH=x86_64
USER root
RUN set -eux; \
    mkdir -p /usr/local/lib/docker/cli-plugins; \
    curl -fsSL \
      "https://github.com/docker/compose/releases/download/${COMPOSE_VERSION}/docker-compose-linux-${TARGETARCH}" \
      -o /usr/local/lib/docker/cli-plugins/docker-compose; \
    chmod +x /usr/local/lib/docker/cli-plugins/docker-compose; \
    /usr/local/lib/docker/cli-plugins/docker-compose version

# ---- Warm hostedtoolcache (optional but recommended) ------------------------
# Pre-seed the tool cache so setup-python / setup-node are offline no-ops on
# cold pods. Uncomment and extend as needed (adds image size). Alternatively,
# allow egress to api.github.com / objects.githubusercontent.com from the CI
# node so the setup-* actions can fetch on demand.
# ENV AGENT_TOOLSDIRECTORY=/opt/hostedtoolcache
# RUN mkdir -p /opt/hostedtoolcache && chown -R runner:runner /opt/hostedtoolcache
# ... (bake Python 3.10/3.11 + Node 20 here) ...

USER runner
