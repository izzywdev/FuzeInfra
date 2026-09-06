#!/usr/bin/env bash
# Label the DURABLE nodes so Longhorn places replicas only on them, and verify
# the cluster is in the state the platform expects.
#
# WHY THIS EXISTS
# Longhorn runs with `createDefaultDiskLabeledNodes: true`
# (argocd/applications/longhorn.yaml), which means a node contributes a replica
# disk ONLY if it carries `node.longhorn.io/create-default-disk=true`. That
# label is node-local state — it is NOT in Git and it does NOT survive a node
# reinstall. Every time a durable node was reinstalled during the private-VLAN
# cutover the label was silently lost, and Longhorn then had too few schedulable
# disks to place 3 replicas ("ReplicaSchedulingFailure ... insufficient
# storage" / "disks are unavailable"), which stalls every affected volume.
#
# Run this after: initial cluster bring-up, ANY node reinstall, or adding a
# durable node. It is idempotent and safe to re-run.
#
#   ./scripts/label-durable-nodes.sh                 # label + verify (default nodes)
#   DURABLE_NODES="a b c" ./scripts/label-durable-nodes.sh
#   ./scripts/label-durable-nodes.sh --verify-only
#
# NEVER label an ephemeral/autoscaled elastic node durable: its Longhorn engine
# is short-lived, and a promoted elastic hung a live Postgres volume in prod
# (2026-07-24) — see docs/runbooks/k3s-ha-etcd-migration.md.
set -euo pipefail

LABEL="node.longhorn.io/create-default-disk=true"
# The three durable (replica-hosting) nodes. Override via DURABLE_NODES.
DURABLE_NODES="${DURABLE_NODES:-vmi3383846 vmi3396106 mendys-worker-1}"

# The durable node that carries the heavy-I/O monitoring stack (Prometheus TSDB +
# Loki), and which is therefore designated the LOWEST-priority holder of the API
# floating VIP -- it should be the last node to serve API traffic, never the first.
#
# WHY: on 2026-09-06 Loki sat on vmi3383846, the same node serving the API. Its
# chunk flushes plus Prometheus TSDB compaction starved etcd off the disk ("apply
# request took too long ... read-only range"), the apiserver's own readiness then
# failed, and it refused connections cluster-wide. Separating "the node that serves
# the API" from "the node that does bulk disk I/O" is the structural fix;
# nodeConfigurator.ioPriority (iocost) is the belt to this braces.
#
# Monitoring cannot simply leave the durable pool -- Longhorn disks exist ONLY on
# durable nodes, so an elastic node can never attach these volumes.
#
# This MUST stay consistent with the keepalived VRRP priorities once the API
# floating VIP lands: this node gets the lowest priority.
MONITORING_LABEL="fuzeinfra.io/role=monitoring"
MONITORING_NODE="${MONITORING_NODE:-vmi3396106}"
VERIFY_ONLY="${1:-}"

kubectl version --request-timeout=10s >/dev/null 2>&1 || {
  echo "ERROR: kubectl cannot reach a cluster" >&2; exit 1; }

if [ "$VERIFY_ONLY" != "--verify-only" ]; then
  echo "== labelling durable nodes =="
  for n in $DURABLE_NODES; do
    if kubectl get node "$n" >/dev/null 2>&1; then
      kubectl label node "$n" "$LABEL" --overwrite >/dev/null
      echo "  ok   $n"
    else
      echo "  SKIP $n (not in cluster)"
    fi
  done

  echo "== labelling the monitoring / lowest-VIP-priority node =="
  if kubectl get node "$MONITORING_NODE" >/dev/null 2>&1; then
    kubectl label node "$MONITORING_NODE" "$MONITORING_LABEL" --overwrite >/dev/null
    echo "  ok   $MONITORING_NODE ($MONITORING_LABEL)"
  else
    # Loud, because Prometheus and Loki nodeSelect on this label: without it they
    # are unschedulable rather than merely misplaced.
    echo "  WARN $MONITORING_NODE not in cluster - Prometheus/Loki will stay Pending" >&2
  fi
fi

echo "== verification =="
labelled=$(kubectl get nodes -l node.longhorn.io/create-default-disk=true \
             --no-headers 2>/dev/null | wc -l | tr -d ' ')
echo "  nodes labelled durable: $labelled"
[ "$labelled" -ge 3 ] || {
  echo "  WARN: Longhorn needs >=3 durable nodes for 3-replica volumes." >&2; }

# Cordoned nodes are excluded from Longhorn replica scheduling. Leftover
# troubleshooting cordons silently starve placement — surface them.
cordoned=$(kubectl get nodes --no-headers 2>/dev/null | grep -c SchedulingDisabled || true)
[ "$cordoned" -eq 0 ] && echo "  cordoned nodes: 0" \
  || echo "  WARN: $cordoned cordoned node(s) — Longhorn will not schedule replicas there:
$(kubectl get nodes --no-headers | awk '/SchedulingDisabled/{print "    "$1}')"

if kubectl get nodes.longhorn.io -n longhorn-system >/dev/null 2>&1; then
  echo "  Longhorn nodes with a schedulable disk:"
  kubectl -n longhorn-system get nodes.longhorn.io -o json 2>/dev/null | python3 -c '
import json,sys
for n in json.load(sys.stdin)["items"]:
    ds=n.get("status",{}).get("diskStatus",{})
    ok=sum(1 for d in ds.values()
           if any(c["type"]=="Schedulable" and c["status"]=="True" for c in d.get("conditions",[])))
    if ok: print(f"    {n[\"metadata\"][\"name\"]}: {ok} disk(s)")
' || true
fi
echo "done."
