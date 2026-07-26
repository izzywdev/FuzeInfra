# Runbook — k3s single-server (SQLite) → HA embedded etcd

**Status:** ready to execute (not yet run). Author: 2026-07-26.
**Why:** The prod cluster is a **single k3s server on SQLite** (`vmi3383846` /
`161.97.118.134`). That control-plane is the SPOF, and it blocks the last step of
the private-VLAN cutover: reinstalling the CP onto eth1 can't be done safely while
it's the only server (a failed server reinstall bricks the cluster). Converting to
an **HA embedded-etcd control-plane (3 servers)** fixes the SPOF **and** makes any
single control-plane reinstall a drain-and-replace instead of a gamble.

> This runbook changes the **control plane only**. Workloads (all DBs on Longhorn)
> keep running throughout — a k3s *server* restart does not evict pods. The genuine
> risk is to the **datastore/API**, mitigated by snapshots + a rollback at every phase.

---

## 0. Current state (verified 2026-07-26)

| Item | Value |
|---|---|
| Control-plane | `vmi3383846`, public `161.97.118.134`, Contabo instance `203383846` |
| Datastore | **SQLite** (`/var/lib/rancher/k3s/server/db/state.db`, ~90 MB) — no `db/etcd/` dir |
| k3s version | `v1.36.2+k3s1` |
| flannel | `wireguard-native` (UDP 51820); tls-san = `161.97.118.134` |
| CP taint | `node-role.kubernetes.io/control-plane=:PreferNoSchedule` |
| Durable nodes on eth1 already | `mendys-worker-1`, `vmi3396106` (agents), `fuzeinfra-elastic-0` (promoted) |
| Server token | `/var/lib/rancher/k3s/server/token` — **encrypts confidential data in the datastore; must be preserved** |
| Latest SQLite copy | `/root/k3s-state-20260726-135831.db` (on CP) |

**Server candidates for the 2 new control-plane members:** `mendys-worker-1` and
`vmi3396106`. They are already reinstalled onto eth1, hold Longhorn replicas, and are
durable. Trade-off: co-locating control-plane + DB replicas on 8 GB nodes adds etcd
load — acceptable short-term (etcd for this small cluster is light), but see §6.

---

## 1. Decisions to confirm before starting

1. **Datastore backend:** embedded etcd (this runbook) vs an **external DB**
   (Postgres/MySQL). Embedded etcd is simplest and needs no extra service, but is
   disk-latency sensitive. For a 3-node Contabo NVMe cluster this is fine. → **embedded etcd.**
2. **Which nodes become the 2 extra servers:** promote `mendys-worker-1` +
   `vmi3396106` (reuse, no new spend) **or** provision 2 fresh durable nodes.
   → default: **promote the two existing durable workers** (they're on eth1).
3. **Join endpoint / tls-san:** additional servers join a **fixed** registration
   address. Today tls-san is only the CP public IP. Add the eth1 IPs and a stable
   name to tls-san *before* others join (§3) so certs are valid when the overlay
   later flips to eth1.
4. **Maintenance window:** ~60–90 min, low-traffic. API has brief (<1 min) blips on
   each server restart; workloads keep serving.

---

## 2. Pre-flight (no changes yet)

Run on the CP (`161.97.118.134`) unless noted.

```bash
# 2.1 Confirm datastore is still SQLite (no etcd dir)
ls /var/lib/rancher/k3s/server/db/etcd 2>/dev/null && echo "ALREADY ETCD — skip §3" || echo "SQLite — proceed"

# 2.2 Cluster is green
kubectl get nodes -o wide
kubectl -n fuzeinfra get pods | grep -vE "Running|Completed" || echo "all workloads Running"

# 2.3 Every Longhorn volume healthy with 3 replicas (control-plane work shouldn't
#     touch these, but we want a known-good baseline)
kubectl -n longhorn-system get volumes.longhorn.io \
  -o custom-columns=VOL:.metadata.name,STATE:.status.state,ROB:.status.robustness

# 2.4 FULL datastore + token backup (THE rollback net). Copy OFF the node too.
TS=$(date +%Y%m%d-%H%M%S)
systemctl stop k3s                         # brief API downtime; DBs keep running
cp -a /var/lib/rancher/k3s/server/db   /root/k3s-db-backup-$TS
cp -a /var/lib/rancher/k3s/server/token /root/k3s-token-backup-$TS
systemctl start k3s
kubectl get nodes >/dev/null && echo "API back"
# scp both off-node:
#   scp -r root@CP:/root/k3s-db-backup-$TS ./ ; scp root@CP:/root/k3s-token-backup-$TS ./
```

> **Do not proceed** until 2.4's backup exists on the CP **and** off-node. The token
> is as important as the db — without it the db copy is undecryptable.

---

## 3. Phase 1 — migrate the CP: SQLite → embedded etcd

Per k3s docs, restarting the server with `--cluster-init` converts a single-server
SQLite datastore to embedded etcd. **The migration is the highest-risk step** —
snapshot is from §2.4.

```bash
# 3.1 Expand tls-san FIRST so future servers/endpoints have valid certs.
#     Edit /etc/rancher/k3s/config.yaml on the CP:
#       tls-san:
#         - 161.97.118.134          # existing public
#         - <CP eth1 IP, e.g. 10.0.0.x>
#         - <stable name if any>
#     (Adding cluster-init below; keep flannel-backend + node-taint as-is.)

# 3.2 Add cluster-init to the server config (persisted, not just a flag):
#     append to /etc/rancher/k3s/config.yaml:
#       cluster-init: true

# 3.3 Restart k3s to trigger the SQLite->etcd migration
systemctl restart k3s
journalctl -u k3s -f            # watch for etcd bootstrap + "etcd data store connection OK"

# 3.4 VERIFY the migration actually happened (docs are ambiguous on auto-migrate)
ls -d /var/lib/rancher/k3s/server/db/etcd && echo "etcd dir present"
kubectl get nodes                                  # API up
kubectl get ns && kubectl -n fuzeinfra get sts     # cluster objects intact (were they migrated?)
k3s etcd-snapshot save                             # first etcd snapshot = proves etcd works
```

**GATE:** `kubectl get` must return the **same** objects as before (namespaces,
StatefulSets, secrets). If the object set is empty/wrong → the SQLite data did **not**
migrate → **STOP and roll back (§R1).** Do not add servers onto an empty etcd.

---

## 4. Phase 2 — add 2 control-plane servers (→ 3-node etcd quorum)

etcd needs an **odd** number of voting members; go from 1 → **3**. All servers must
share identical `--flannel-backend`, `--cluster-cidr`, `--cluster-dns`,
`--secrets-encryption`, and use the **same token** (`/var/lib/rancher/k3s/server/token`).

Two ways to add each server:

### Option A — promote the existing durable workers (default; no new spend)
`mendys-worker-1` and `vmi3396106` are agents today. A node's server/agent role is
fixed at join, so each must **re-join as a server**. Do them **one at a time**,
waiting for etcd health between them.

Per node (example `vmi3396106`):
```bash
# 4A.1 Data-safety: confirm every Longhorn volume has 2+ replicas OFF this node
#      (same check used in the VLAN worker reinstalls).
# 4A.2 Drain it (workloads move to the other 3 durable nodes)
kubectl cordon <node> && kubectl drain <node> --ignore-daemonsets --delete-emptydir-data \
  --pod-selector app.kubernetes.io/part-of=fuzeinfra --timeout=120s
# 4A.3 Reinstall/rejoin as a SERVER (NOT the agent salvage-enroll template).
#      cloud-init / k3s install must run:
#        curl -sfL https://get.k3s.io | INSTALL_K3S_VERSION=v1.36.2+k3s1 sh -s - server \
#          --server https://161.97.118.134:6443 \
#          --token <SERVER_TOKEN> \
#          --flannel-backend wireguard-native \
#          --node-taint node-role.kubernetes.io/control-plane=:PreferNoSchedule \
#          --node-name <node>
#      (clear the node-password secret + delete the node object first, as in the
#       VLAN worker procedure, so it re-registers.)
# 4A.4 Wait: kubectl get node <node> => Ready + control-plane role; then
kubectl get nodes -l node-role.kubernetes.io/control-plane=true
k3s etcd-snapshot save   # snapshot after EACH member joins
# 4A.5 Re-label durable (node.longhorn.io/create-default-disk=true) + wait Longhorn rebuild
#      to healthy BEFORE promoting the second node.
```
Repeat for the second node.

### Option B — provision 2 fresh durable server nodes
Same `k3s ... server --server ... --token ...` join, on 2 new Contabo instances built
with the durable (eth1 + open-iscsi + /var/lib/longhorn) cloud-init but a **server**
(not agent) k3s command. No drain needed; just join, label durable, let Longhorn
place replicas. Costs 2 more nodes but keeps etcd off the DB-heavy workers.

**GATE (end of Phase 2):** exactly 3 Ready control-plane nodes; etcd healthy:
```bash
kubectl get nodes -l node-role.kubernetes.io/control-plane=true      # 3 Ready
k3s etcd-snapshot save && echo "etcd writable with 3 members"
# member list (on any server):
kubectl -n kube-system get endpoints kube-scheduler -o yaml >/dev/null   # API steady
```

---

## 5. Phase 3 — now the VLAN cutover can finish safely

With 3 servers, losing one is non-fatal. Complete the deferred VLAN work:

1. **Reinstall the original CP `vmi3383846` onto eth1** — but now as a *drain-and-replace*:
   the other 2 servers keep the API up. Drain it, reinstall with the **server** join
   (Option A/B command, `--server https://<one of the other servers>:6443`), rejoin,
   relabel. No datastore-restore gamble — it re-syncs from the etcd quorum.
2. **Stage-2 flannel flip** (all nodes now on eth1): set `--flannel-iface eth1` +
   `--node-ip <private>` + `--node-external-ip <public>` on every node, restart k3s in
   one tight window. See `project_private_vlan_cutover` memory.

---

## 6. Risks / gotchas (FuzeInfra-specific)

- **SQLite→etcd auto-migrate is under-documented.** Treat §3.4's GATE as mandatory.
  If objects don't carry over, roll back — do not rebuild from an empty etcd.
- **Token is load-bearing.** `/var/lib/rancher/k3s/server/token` encrypts datastore
  secrets. Every server shares it; back it up with the db (§2.4). Losing it = the
  snapshot is undecryptable.
- **etcd on 8 GB DB nodes (Option A).** Co-locating an etcd member with Longhorn
  replicas + DB pods raises memory/IO pressure (recall the 2026-07-24 OOM). Watch
  `vmi3396106` (emergency swap) — prefer Option B if capacity is tight, or ensure the
  4th durable node (elastic-0) absorbs drained DBs during Phase 2.
- **flannel wireguard-native must match on all servers** or the overlay breaks.
- **Longhorn during control-plane work:** server restarts don't move pods, but the
  Phase-2 **drains** (Option A) do — reuse the VLAN data-safety gate (every volume 2+
  replicas off the node, no local-path PVC pinned) before each drain.
- **Argo selfHeal** is unrelated to the datastore but keep it ON; none of this touches
  Helm-managed objects.

---

## R. Rollback

### R1 — Phase 1 migration failed / etcd empty or unhealthy
```bash
systemctl stop k3s
# restore the SQLite db + token from §2.4
rm -rf /var/lib/rancher/k3s/server/db
cp -a /root/k3s-db-backup-<TS>   /var/lib/rancher/k3s/server/db
cp -a /root/k3s-token-backup-<TS> /var/lib/rancher/k3s/server/token
# remove cluster-init + etcd remnants from config.yaml (back to SQLite single-server)
sed -i '/cluster-init/d' /etc/rancher/k3s/config.yaml
rm -rf /var/lib/rancher/k3s/server/db/etcd 2>/dev/null || true
systemctl start k3s
kubectl get ns   # back to pre-migration SQLite state
```

### R2 — a new server joined badly / etcd quorum unhealthy after Phase 2
Because snapshots were taken after each join, restore the last-good etcd snapshot
(k3s multi-server restore):
```bash
# on ALL servers:
systemctl stop k3s
# on the primary holding the snapshot:
k3s server --cluster-reset --cluster-reset-restore-path=/var/lib/rancher/k3s/server/db/snapshots/<snap>
systemctl start k3s          # wait healthy
# on the OTHER servers:
rm -rf /var/lib/rancher/k3s/server/db/
systemctl start k3s
```
(k3s moves current etcd files to `db/etcd-old-<TS>/` automatically.)

### R3 — total loss of the control plane
Rebuild a single SQLite server from the §2.4 backup (R1 procedure on a fresh CP
instance), then re-attach agents. Workloads' data survives on Longhorn regardless.

---

## Appendix — commands proven this session
- On-demand etcd snapshot: `k3s etcd-snapshot save`
- Restore: `systemctl stop k3s` → `k3s server --cluster-reset --cluster-reset-restore-path=<path>` → `systemctl start k3s` (+ `rm -rf db/` on other servers)
- SQLite datastore path: `/var/lib/rancher/k3s/server/db/`  · token: `/var/lib/rancher/k3s/server/token`
- Add server: `... server --server https://<server>:6443 --token <token>` (identical net/feature flags)
- Worker→VLAN reinstall pattern (agents) + node-password fix: see `project_private_vlan_cutover` memory.
