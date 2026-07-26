# Runbook — prod cluster from zero (HA + private VLAN + Longhorn)

The end-state this reproduces, and where each piece is codified. Everything below
is either **automatic** (Git/Helm/Argo/cloud-init) or a **single scripted step** —
no ad-hoc `kubectl` archaeology. Written 2026-07-26 after the HA + private-VLAN
cutover, so the manual steps discovered that day never have to be rediscovered.

**Target state:** 3-node embedded-etcd control plane (no SPOF) · every node's pod
overlay on the Contabo private VLAN (net 60932, `10.0.0.0/22`) · Longhorn 3-replica
storage pinned to durable nodes · all DB StatefulSets on `longhorn` · nightly
logical backups · Argo CD `automated{prune,selfHeal}`.

---

## 1. Nodes (Terraform / cloud-init) — automatic

| Node class | Template | Joins as | Private VLAN |
|---|---|---|---|
| elastic (autoscaled) | `deploy/elastic-userdata-eth1.template` | agent | yes — `flannel-iface: eth1` written pre-join |
| durable worker | `deploy/durable-userdata-eth1.template` | agent | yes — same, plus open-iscsi/nfs + `/var/lib/longhorn` |
| control plane | `deploy/cp-userdata-eth1.template` | **prepared only** | yes — operator then writes `config.yaml` (§2) |

All three write `/etc/rancher/k3s/config.yaml` with **`flannel-iface: eth1`** *before*
the k3s join, so a node comes up on the VLAN directly — there is no "Stage 2 flip"
for new nodes. They also `ufw allow from 10.0.0.0/22` and open `51820/udp`
(flannel wireguard-native).

> **The Contabo private NIC (`eth1`) only appears after a REINSTALL.** A reboot is
> not enough — confirmed twice. Any node that must move onto the VLAN gets
> reinstalled via the `ca-salvage-enroll` workflow with the matching `-eth1` template.

## 2. Control plane / HA — scripted

First server: `cluster-init: true` in `/etc/rancher/k3s/config.yaml` (migrates an
existing SQLite datastore to embedded etcd in place). Servers 2 and 3 join with a
`config.yaml` carrying `server: https://<peer>:6443` + the server token. Full
procedure, gates and rollback: **`docs/runbooks/k3s-ha-etcd-migration.md`**.

Two rules that cost real downtime when violated:

- **Never pass `--server`/`--token` as cloud-init CLI args** — they can arg-mangle
  and the node bootstraps *its own* etcd cluster (split-brain). Always `config.yaml`.
- **Never set `node-ip` on a server.** It changes the node's expected etcd peer URL
  and k3s refuses to start (`this server is not a member of the etcd cluster …
  expect: <name>=https://<ip>:2380`). To move a server's peer onto the VLAN, update
  it in etcd **first**, from a peer:
  `etcdctl member update <id> --peer-urls=https://<vlan-ip>:2380`.
  Agents may set `node-ip`/`node-external-ip` freely.

Firewall between servers (in the CP template; add by hand for pre-existing nodes):
`2379:2380/tcp` (etcd), `6443/tcp`, `10250/tcp`, and `from 10.0.0.0/22`.

## 3. Longhorn — Git + one script

Chart + settings: `argocd/applications/longhorn.yaml` (`defaultSettings`) — 3
replicas, hard anti-affinity, `createDefaultDiskLabeledNodes: true`,
`storageOverProvisioningPercentage: 100`, `replicaReplenishmentWaitInterval: 30`.

**The one piece that is NOT in Git** is the per-node durable label — it is node-local
and does **not survive a reinstall**:

```bash
./scripts/label-durable-nodes.sh          # run after bring-up, ANY node reinstall, or adding a durable node
./scripts/label-durable-nodes.sh --verify-only
```

Without the label the node contributes no replica disk, and volumes stall with
`ReplicaSchedulingFailure … insufficient storage` / `disks are unavailable`.

**Never promote an elastic node to durable.** A promoted elastic's Longhorn engine
hung a live Postgres volume in prod and only a hard reboot released it.

## 4. Workloads — Git (Argo)

Already declarative in `helm/fuzeinfra`; listed so a from-zero operator knows they
are covered: `global.storageClass: longhorn` (prod) so any StatefulSet recreation
lands on durable storage · `postgres.nodeSelector` / `neo4j.nodeSelector` pinning
DBs to durable nodes · `fuzeinfra.dbSpread` soft anti-affinity so DBs don't pile
onto one node · kafka `fsGroup: 1000` + init-chown (fresh ext4 Longhorn volumes are
root-owned) · `backups.sink: pvc` nightly `pg_dumpall`/`mongodump`/neo4j-APOC to a
Longhorn PVC · Argo `ignoreDifferences` on StatefulSet `.spec.volumeClaimTemplates`
(the immutable field that once caused DBs to be recreated onto empty local-path PVCs).

## 5. Verify

```bash
kubectl get nodes                                            # all Ready
kubectl get nodes -l node-role.kubernetes.io/etcd=true       # 3 servers
kubectl get nodes -o json | python3 -c 'import json,sys;n=json.load(sys.stdin)["items"];print(sum(1 for x in n if x["metadata"].get("annotations",{}).get("flannel.alpha.coreos.com/public-ip","").startswith("10.0.")),"/",len(n),"on VLAN")'
./scripts/label-durable-nodes.sh --verify-only               # >=3 durable, 0 cordoned
kubectl -n longhorn-system get volumes.longhorn.io           # attached + healthy
```

## 6. Troubleshooting notes worth keeping

- **A volume won't attach** → check the *volume's* `Scheduled` condition first
  (`kubectl -n longhorn-system get volumes.longhorn.io <vol> -o json`). The
  csi-attacher message `volume … is not ready for workloads` means it cannot place
  replicas — **not** that CSI is broken. Usual causes: too few labelled durable
  nodes, leftover **cordons** (Longhorn skips cordoned nodes), or disks full of
  **orphaned volumes** from earlier PVC recreations (delete Longhorn volumes not
  bound by any PVC — they keep consuming scheduled space).
- **`mke2fs … is apparently in use by the system`** on mount = stale device mapping
  on that node; move the pod to another node (or clear the device).
- After clearing a backlog, `kubectl -n longhorn-system rollout restart
  deployment/csi-attacher` clears the exponential retry backoff.
