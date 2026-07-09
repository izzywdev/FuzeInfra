# syntax=docker/dockerfile:1
# =============================================================================
# FuzeInfra ARC runner image
#
# Base:  ghcr.io/actions/actions-runner  (GitHub's official ephemeral runner)
# Adds:  Docker Compose V2 plugin  →  `docker compose ...` works inside jobs.
#
# Why: the base runner (with the ARC dind sidecar) ships the `docker` CLI but
# NOT the compose plugin, so `docker compose -f ... up` fails with
# `unknown shorthand flag: 'f' in -f` — `docker` parses `compose` as an
# unknown positional and `-f` as a docker flag.  See izzywdev/FuzeInfra#223.
#
# Build & push:  runners/arc/build-runner-image.sh
# Consumed by:   runners/arc/runner-scale-set-values.yaml (FuzeInfra `staging`)
#                runners/arc/register-repo.sh             (all consumer scale sets)
# =============================================================================
ARG BASE_TAG=latest
FROM ghcr.io/actions/actions-runner:${BASE_TAG}

USER root

# Install the Docker Compose V2 plugin from Docker's official apt repository.
# We wire up the repo explicitly rather than assuming the base image already has
# it, so the build is reproducible regardless of the base image's apt config.
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends ca-certificates curl gnupg; \
    install -m 0755 -d /etc/apt/keyrings; \
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
      | gpg --dearmor -o /etc/apt/keyrings/docker.gpg; \
    chmod a+r /etc/apt/keyrings/docker.gpg; \
    . /etc/os-release; \
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu ${VERSION_CODENAME} stable" \
      > /etc/apt/sources.list.d/docker.list; \
    apt-get update; \
    apt-get install -y --no-install-recommends docker-compose-plugin; \
    rm -rf /var/lib/apt/lists/*; \
    # Verify the plugin landed on the CLI plugin path (no daemon needed at build).
    test -x /usr/libexec/docker/cli-plugins/docker-compose

# Drop back to the unprivileged runner user the base image expects.
USER runner
