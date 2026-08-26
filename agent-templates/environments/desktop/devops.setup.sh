#!/bin/bash
# DevOps — Claude Code cloud environment setup script.
# GitOps slice: edit Helm/Argo/values, lint + validate, open a PR. Deliberately has NO prod cluster access.
#
# GENERATED from agent-templates/environments/cloud-devops.json by
# agent-templates/environments/desktop/render.py — do not edit by hand.
# Paste into the Setup script field of the environment dialog at claude.ai/code.
#
# Must exit 0: a non-zero exit makes the session fail to start, so every install
# is || true. Independent installs run in parallel to stay under the ~5 min budget.
set -u

# apt is serialised — dpkg holds a global lock, so parallel installs deadlock.
echo "[setup] apt"
apt-get update -qq || true
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq gh || true

echo "[setup] cloudflared"
install -d -m 0755 /usr/share/keyrings
curl -fsSL https://pkg.cloudflare.com/cloudflare-main.gpg -o /usr/share/keyrings/cloudflare-main.gpg || true
echo 'deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] https://pkg.cloudflare.com/cloudflared any main' > /etc/apt/sources.list.d/cloudflared.list
apt-get update -qq || true
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq cloudflared || true

# Independent of each other — run concurrently, then wait.
( echo "[setup] pip"; pip install --quiet --no-input yamllint check-jsonschema pyyaml a2a-sdk 'mcp>=1.9,<2' || pip install --quiet --no-input --break-system-packages yamllint check-jsonschema pyyaml a2a-sdk 'mcp>=1.9,<2' || true ) &
( echo "[setup] go github.com/yannh/kubeconform/cmd/kubeconform@latest"; GOBIN=/usr/local/bin go install github.com/yannh/kubeconform/cmd/kubeconform@latest || true ) &
( echo "[setup] go github.com/bitnami-labs/sealed-secrets/cmd/kubeseal@latest"; GOBIN=/usr/local/bin go install github.com/bitnami-labs/sealed-secrets/cmd/kubeseal@latest || true ) &
( echo "[setup] helm"
  HELM_VER="$(curl -fsSL https://get.helm.sh/helm-latest-version 2>/dev/null | tr -d '[:space:]')"
  if [ -n "${HELM_VER:-}" ] \
     && curl -fsSL "https://get.helm.sh/helm-${HELM_VER}-linux-amd64.tar.gz" -o /tmp/helm.tgz \
     && tar -xzf /tmp/helm.tgz -C /tmp linux-amd64/helm; then
    install -m 0755 /tmp/linux-amd64/helm /usr/local/bin/helm || true
  else
    echo "[setup] helm: get.helm.sh unavailable, building from source" >&2
    GOBIN=/usr/local/bin go install helm.sh/helm/v3/cmd/helm@latest || true
  fi ) &
wait

# Leave a record in the session log of what actually landed.
echo "[setup] installed:"
for b in gh kubeconform kubeseal helm yamllint check-jsonschema cloudflared; do
  printf "  %-12s %s\n" "$b" "$(command -v "$b" 2>/dev/null || echo MISSING)"
done

# Always succeed: a failed optional install must not block the session.
exit 0
