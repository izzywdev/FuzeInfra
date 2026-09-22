#!/usr/bin/env bash
# Reconcile the CI runner nodes' pool identity so every dedicated CI node carries
# fuzeinfra.io/pool=ci (plus the matching role labels), and verify the result.
#
# WHY THIS EXISTS
# fuzeinfra.io/pool is a k3s REGISTRATION-time label. cloud-init sets it from the
# node's role on first boot (`--node-label fuzeinfra.io/pool=${role}`, see
# modules/contabo-k3s-node/cloud-init.tftpl) and NOTHING reconciles it afterwards:
# Argo does not manage node labels, and Terraform's ignore_changes=[user_data]
# means a re-render never re-labels a running node.
#
# So a CI node provisioned BEFORE that cloud-init existed keeps whatever label was
# hand-set at the time. fuzeinfra-ci-runner-1 (created 2026-07-24, on the retired
# V92 SKU that Contabo will no longer sell — "No offer was found for product ID
# V92" — so it CANNOT be destroyed+recreated to re-run cloud-init) carries a stale
# `fuzeinfra.io/pool=durable`. ARC runner pods require
#     nodeAffinity: fuzeinfra.io/pool In [ci, elastic]
# (runners/arc/runner-scale-set-values.yaml), so a CI node mislabelled `durable` is
# silently excluded from ALL runner scheduling — it stays Ready, holds the CI taint,
# and picks up zero jobs while every runner piles onto the other ci node.
#
# This is the reconciling backstop for that label, mirroring
# scripts/label-durable-nodes.sh. Idempotent and safe to re-run.
#
#   ./scripts/reconcile-ci-pool-labels.sh                # reconcile + verify
#   ./scripts/reconcile-ci-pool-labels.sh --verify-only  # report only
#
# Note: k3s applies a --node-label only at agent REGISTRATION, never on restart, so
# the label this script sets persists across kubelet/k3s restarts. The host's own
# registration args are only re-read if the node is deleted and re-joins; a durable
# host-level correction (fixing pool=durable -> pool=ci in the node's k3s config)
# belongs in that node's provisioning source and is out of scope here.
set -euo pipefail

POOL_LABEL_KEY="fuzeinfra.io/pool"
CI_TAINT_KEY="fuzeinfra.io/ci"

# CI nodes are DISCOVERED by the taint the platform reserves for them, NOT by name
# or by the pool label we are trying to fix. fuzeinfra.io/ci=true:NoSchedule is
# applied at registration, survives renames/reinstalls, and can never match an
# elastic node (those carry fuzeinfra.io/elastic) or a durable/control-plane node.
# So discovery can never mislabel autoscaled elastic capacity as a fixed ci node —
# the exact invariant scripts/label-durable-nodes.sh preserves for the durable pool.
discover_ci_nodes() {
  kubectl get nodes -o json 2>/dev/null | python3 -c '
import json, sys
try:
    items = json.load(sys.stdin).get("items", [])
except Exception:
    sys.exit(0)
key = "'"$CI_TAINT_KEY"'"
for n in items:
    taints = n.get("spec", {}).get("taints") or []
    if any(t.get("key") == key for t in taints):
        print(n.get("metadata", {}).get("name", ""))
'
}

CI_NODES="${CI_NODES:-$(discover_ci_nodes)}"
if [ -z "${CI_NODES// /}" ]; then
  # Fail closed. Reconciling nothing would look like success while leaving a
  # mislabelled CI node stranded and out of the runner pool.
  echo "ERROR: no CI nodes discovered (none carry the ${CI_TAINT_KEY} taint) and" >&2
  echo "       CI_NODES not set. Refusing to run: reconciling zero nodes is" >&2
  echo "       indistinguishable from success." >&2
  exit 1
fi

VERIFY_ONLY="${1:-}"

kubectl version --request-timeout=10s >/dev/null 2>&1 || {
  echo "ERROR: kubectl cannot reach a cluster" >&2; exit 1; }

if [ "$VERIFY_ONLY" != "--verify-only" ]; then
  echo "== reconciling CI pool labels (${POOL_LABEL_KEY}=ci) =="
  for n in $CI_NODES; do
    if kubectl get node "$n" >/dev/null 2>&1; then
      cur=$(kubectl get node "$n" -o jsonpath="{.metadata.labels.${POOL_LABEL_KEY//./\\.}}" 2>/dev/null || true)
      # role/node-role are set alongside pool so a legacy node fully matches the
      # label set a current cloud-init'd ci node has (see cloud-init.tftpl); all
      # three are idempotent overwrites.
      kubectl label node "$n" \
        "${POOL_LABEL_KEY}=ci" \
        "fuzeinfra.io/role=ci" \
        "node-role=ci" \
        --overwrite >/dev/null
      if [ "$cur" = "ci" ]; then
        echo "  ok    $n (already ${POOL_LABEL_KEY}=ci)"
      else
        echo "  FIXED $n (${POOL_LABEL_KEY}: '${cur:-<none>}' -> ci)"
      fi
    else
      echo "  SKIP  $n (not in cluster)"
    fi
  done
fi

echo "== verification =="
bad=0
for n in $CI_NODES; do
  kubectl get node "$n" >/dev/null 2>&1 || continue
  pool=$(kubectl get node "$n" -o jsonpath="{.metadata.labels.${POOL_LABEL_KEY//./\\.}}" 2>/dev/null || true)
  if [ "$pool" = "ci" ]; then
    echo "  OK   $n  ${POOL_LABEL_KEY}=ci"
  else
    echo "  BAD  $n  ${POOL_LABEL_KEY}=${pool:-<none>} (expected ci)"
    bad=$((bad + 1))
  fi
done
if [ "$bad" -ne 0 ]; then
  echo "  $bad CI node(s) still not ${POOL_LABEL_KEY}=ci" >&2
  exit 1
fi
echo "done."
