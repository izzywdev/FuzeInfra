# Dark-node detection and reboot

## Why this exists

A Contabo-autoscaler-health alert investigation (2026-09-15) found
`fuze-core-3` — a control-plane/etcd node — silently dark: `Ready`,
`MemoryPressure`, `DiskPressure` and `PIDPressure` all `Unknown` ("Kubelet
stopped posting node status") for **4.5 days**. It still held its etcd voting
membership and its scheduled pods the entire time while contributing zero
usable capacity, and nothing had ever flagged it. Same "everything reports
fine, nothing actually works" shape as the 2026-08-30 Loki/Argo freeze that
[`deployment-watchdog.yml`](../../.github/workflows/deployment-watchdog.yml)
already exists for, so the detection lives there too.

## The flow

```
deployment-watchdog.yml (every 15m, read-only)
  -> scripts-tools/deployment_watchdog.py: detect_dark_nodes()
  -> Node Ready condition == Unknown for > dark_node.unknown_minutes (default 30)?
       -> files a GitHub issue mentioning @fuze, with facts:
            node, unknown_since, unknown_minutes, internal_ip, external_ip,
            is_control_plane, is_etcd_voter, other_ready_control_plane_nodes
  -> @fuze (fuze-cluster.yml, cluster-capable) reads the issue and DECIDES
  -> if safe to restart: dispatches contabo-instance-reboot.yml
       (node_name, external_ip, reason, confirm=RESTART, issue_number)
  -> contabo-instance-reboot.yml resolves the Contabo instanceId by matching
     external_ip against ipConfig.v4.ip, calls
     POST /v1/compute/instances/{id}/actions/restart, comments the result
     back onto the watchdog issue
  -> a human (or @fuze on a later pass) verifies `kubectl get nodes` shows
     Ready=True and closes the issue
```

Detection is read-only and holds no Contabo credential. The reboot itself is
a separate, narrowly-scoped workflow that DOES hold the Contabo credential —
see "Why two workflows" below.

## The decision @fuze has to make

The watchdog does **not** auto-restart, unlike the stuck-Argo-op detection
(which auto-dispatches `argo-terminate-op.yml`, because terminating a stale
sync op is provably safe and reversible — Argo just re-syncs from git).
Rebooting a node is not that: it can matter which workloads are pinned there
and whether the node is an etcd voter. So the filed issue hands @fuze the
facts it needs to decide instead of deciding for it:

- **`is_control_plane` + `is_etcd_voter` + `other_ready_control_plane_nodes`**
  — the quorum-safety check. Do not restart a control-plane/etcd-voter node
  if it is the only one (or one of only two) currently `Ready` — confirm with
  a fresh `kubectl get nodes` via `cluster-query.yml` before proceeding, since
  the issue's numbers are a snapshot from whenever the watchdog last ran.
- Whether the node has been flagged before (check for a prior closed
  `[watchdog] dark-node: <name>` issue) — a node that goes dark repeatedly is
  a hardware/host problem a reboot will not fix twice.
- Whether anything else explains the darkness (e.g. a Contabo VLAN/network
  incident affecting multiple nodes at once — reboot each independently only
  after ruling out a shared cause, per the private-VLAN gotchas in the top
  level `CLAUDE.md`).

If it decides to proceed:

```bash
gh workflow run contabo-instance-reboot.yml --repo izzywdev/FuzeInfra \
  -f node_name=fuze-core-3 \
  -f external_ip=194.163.136.242 \
  -f reason="Ready=Unknown 4.6d, other_ready_control_plane_nodes=2 — not a quorum risk" \
  -f confirm=RESTART \
  -f issue_number=<the watchdog issue number>
```

If it decides NOT to proceed, it should say why on the issue and leave it
open (or hand it to a human) rather than silently doing nothing.

## Why two workflows, not one

`fuze-cluster.yml` (the cluster-capable `@fuze` responder) already holds prod
`KUBE_CONFIG` and runs an open-ended agent. Giving that same runner the
Contabo credential too would mean one compromised or misdirected agent
session holds both the cluster layer and the hypervisor layer under it.
Instead, `contabo-instance-reboot.yml` holds the Contabo credential and does
exactly one narrow, auditable thing (`confirm=RESTART` required, one Contabo
Action, one instance) — the same split every other `contabo-*.yml` one-off
workflow in this repo already uses (see `contabo-rename-instance.yml`,
`ca-delete-instance.yml`). `@fuze` decides; it does not hold the credential.

## Resolving instanceId by IP

Nodes are not labelled with their Contabo `instanceId` at bootstrap
(`modules/contabo-k3s-node/cloud-init.tftpl` sets none), so the only reliable
link from a Kubernetes Node object to a Contabo instance today is the public
IP: `kubectl get node <name> -o wide` → `EXTERNAL-IP` column, matched against
`GET /v1/compute/instances` → `.data[].ipConfig.v4.ip`. This is what
`contabo-instance-reboot.yml`'s "Resolve instanceId" step does when
`instance_id` is not supplied directly. See
[`.claude/agents/contabo-expert.md`](../../.claude/agents/contabo-expert.md)
for the full Contabo API endpoint map.

## Tuning

Thresholds live in
[`governance/watchdog-thresholds.json`](../../governance/watchdog-thresholds.json)
under `dark_node`. Detection logic (`detect_dark_nodes`) is unit tested
offline in
[`tests/test_deployment_watchdog.py`](../../tests/test_deployment_watchdog.py)
— no cluster required.
