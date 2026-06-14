#!/usr/bin/env bash
# Delete the local FuzeInfra kind cluster.
set -euo pipefail
CLUSTER_NAME="fuzeinfra"
echo "==> Deleting kind cluster '$CLUSTER_NAME'"
kind delete cluster --name "$CLUSTER_NAME"
echo "==> Done."
