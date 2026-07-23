# Design: Longhorn replicated block storage for FuzeInfra prod k3s

Status: **Proposed (design only)** — this document performs **no** `helm install`, no
`kubectl apply`, no data migration, and no prod mutation. Every deploy/migration
step below is explicitly **gated on human review** and a maintenance window.

Author: design synthesis (Claude, session `bfebd23d-f1a8-49d4-ad24-522a1d169855`)
Date: 2026-07-23
Scope: `argocd/applications/`, `helm/fuzeinfra/`, `modules/contabo-k3s-node/`,
`cluster-autoscaler/contabo-externalgrpc/deploy/`.
Companion doc: `docs/design/s3-and-private-networking.md` (S3 = logs+backups+blobs;
databases stay on **block** storage). Longhorn is the missing block-storage piece
that makes those DB block PVs **replicated + node-independent**.

---

## 0. Executive summary (the honest version)

Today every stateful FuzeInfra service runs on the **k3s `local-path`** provisioner:
a PVC is a directory on **one** node's disk, so the pod is **pinned to that node**
for the life of the volume. All four primary databases (Postgres, MongoDB, Neo4j,
Redis) are pinned to the single control-plane node `vmi3383846` on `local-path`
RWO PVCs. That is a **single point of failure** and a **resource hotspot**
(~84% memory on that one node) that structurally blocks three things:

1. **Roadmap item A** (spreading the DBs off the one hot node) — a `local-path`
   PVC cannot move with its pod.
2. **The control-plane private-VLAN migration** — re-IPing / rebooting the
   control-plane means its pinned DB pods go down with it and cannot reschedule.
3. **Control-plane HA / reinstall** — you cannot reinstall or add control-plane
   nodes while irreplaceable data is welded to one node's local disk.

**Longhorn** is a CNCF distributed block-storage system for Kubernetes. It turns
each PVC into a **replicated** block volume (default **3 replicas** spread across
nodes) exposed to the pod as a normal RWO (or RWX-via-NFS) block device. Because a
replica exists on other nodes, **the pod can be rescheduled** to any node that has
(or can rebuild) a replica — which is exactly what unblocks 1–3.

**What this design does NOT claim:** it does not make S3 a filesystem, it does not
move databases onto S3, and it does not flip any existing PVC to Longhorn. Longhorn
is added as an **opt-in `longhorn` StorageClass alongside** the existing default
`local-path`. Migrating each database onto it is a **separate, per-DB, human-gated**
maintenance-window operation (§6), not part of this scope.

**Everything ships default-OFF. Merging the implementation PR is a no-op in prod
until a human (a) installs the node prerequisites, (b) syncs the Longhorn Argo
Application, and (c) enables the StorageClass.**

---

## 1. Current state (verified against the repo)

| Fact | Source |
|---|---|
| Prod is Contabo single-control-plane k3s, ns `fuzeinfra`, Argo CD `automated{prune,selfHeal}`, ServerSideApply | `argocd/applications/fuzeinfra-prod.yaml` |
| Storage is **`local-path` (RWO block) only** — `global.storageClass: "local-path"` | `helm/fuzeinfra/values-contabo.yaml` L15 |
| Postgres/MongoDB/Neo4j/Redis run as StatefulSets with `local-path` PVCs, effectively pinned to the control-plane node | `helm/fuzeinfra/templates/databases.yaml` |
| 10 nodes today: 4 baseline + 6 elastic, all Contabo **V92** (4 vCPU / 8 GB) | given / autoscaler config |
| Elastic nodes carry `fuzeinfra.io/pool=elastic` + `fuzeinfra.io/elastic=true:PreferNoSchedule` taint | `modules/contabo-k3s-node/cloud-init.tftpl`, `elastic-userdata.template` |
| Standalone infra add-ons are their own Argo `Application` (e.g. `sealed-secrets`, `arc-runners`) — NOT folded into the umbrella `fuzeinfra` chart | `argocd/applications/*.yaml` |
| The umbrella chart gates every service behind an `enabled` flag in `values.yaml` and threads overlays | `helm/fuzeinfra/values*.yaml` |
| ELASTIC-EXCLUSION invariant: `terraform/contabo` manages exactly one node and NEVER enumerates the account inventory | `terraform/contabo/main.tf`, s3-and-private-networking.md |
| Node cloud-init installs no iSCSI / NFS client packages today | `modules/contabo-k3s-node/cloud-init.tftpl` |

---

## 2. Deploy model: an **independent Argo Application** (not the umbrella chart)

**Recommendation: deploy Longhorn as its own Argo `Application`
`argocd/applications/longhorn.yaml`, sourcing the upstream Longhorn Helm chart,
with `syncPolicy` set to MANUAL (no `automated` block).** Rationale:

- **Independent lifecycle.** Longhorn is a cluster infra add-on (its own CRDs,
  DaemonSet, CSI driver, manager, UI) with an upstream release cadence decoupled
  from FuzeInfra's app services — exactly the shape already used for
  `sealed-secrets` and `arc-runners`. Folding a CSI driver + CRDs into the
  `fuzeinfra` umbrella chart would couple its selfHeal loop to Longhorn upgrades
  and bloat the one app that owns all the databases.
- **Blast-radius isolation.** A wedged Longhorn sync must never wedge the DB app.
- **Gate correctness.** Manual sync means **merging the manifest into `main` does
  not deploy anything** — the `Application` object is not even created until a
  human runs `kubectl apply -f argocd/applications/longhorn.yaml`, and even then
  Argo will not sync until a human clicks Sync. This is the strongest possible
  default-OFF gate (see §5).

> Why NOT `automated{selfHeal}` at first: Longhorn installs cluster-scoped CRDs and
> a privileged DaemonSet that must land **only after** the node prerequisites (§3)
> exist. Auto-sync before prereqs → CrashLooping engine pods. Turn on `automated`
> **after** the first successful manual sync + validation, in a follow-up PR.

Chart source: `https://charts.longhorn.io`, `chart: longhorn`, pinned
`targetRevision` (e.g. `1.7.x` — pin the exact patch at implementation time and bump
deliberately, matching the `sealed-secrets`/`arc-runners` pinning discipline).
Namespace: `longhorn-system` (Longhorn's required namespace), `CreateNamespace=true`.

Key Helm values (set inline in the Application, see §4):
- `defaultSettings.defaultDataPath: /var/lib/longhorn` — the node data dir (§3).
- `defaultSettings.defaultReplicaCount: 3` — replicate across 3 nodes (§4.1).
- `defaultSettings.replicaSoftAntiAffinity: false` — force replicas onto **distinct
  nodes** (no two replicas of one volume on the same node; else HA is fake).
- `defaultSettings.storageOverProvisioningPercentage` + `storageMinimalAvailablePercentage`
  — sized for the small V92 disks (§4.2).
- `persistence.defaultClass: false` — **do NOT** let Longhorn create a
  default-marked StorageClass; keep `local-path` the cluster default. We ship our
  own non-default `longhorn` StorageClass instead (§4.3).
- `csi.*ReplicaCount: 1` and trimmed `longhornManager`/`longhornDriver` resource
  requests — the control-plane is memory-constrained.
- `preUpgradeChecker.jobEnabled: false` on the first install.

---

## 3. Node prerequisites (the thing that silently breaks Longhorn)

Longhorn attaches volumes over **iSCSI** and serves RWX volumes over **NFS**. Each
node that runs a Longhorn replica or a Longhorn-backed pod MUST have:

1. **`open-iscsi`** installed and the **`iscsid`** service enabled + running
   (the `iscsiadm` binary must be on the host; k3s ships its own kubelet but not
   the iSCSI initiator).
2. **`nfs-common`** (NFSv4 client) — required for any RWX (ReadWriteMany) Longhorn
   volume; harmless to have even if only RWO is used.
3. A writable data directory **`/var/lib/longhorn`** on a filesystem with real free
   space (this is where replica data lives).

Missing any of these → the Longhorn engine/manager pod on that node CrashLoops with
`iscsiadm: command not found` / `failed to mount`, and volumes never attach. This
is the #1 Longhorn install failure and is invisible until you try to attach a PVC.

### 3.1 Two codification paths (both in this design; both gated)

**(a) New nodes — cloud-init.** Codify the packages into the reusable worker module
`modules/contabo-k3s-node/cloud-init.tftpl`, behind a new
`enable_longhorn_prereqs` variable (default **false**). When true, cloud-init runs
`apt-get install -y open-iscsi nfs-common`, `systemctl enable --now iscsid`, and
`mkdir -p /var/lib/longhorn` on first boot. Mirror the same (commented, opt-in)
block into the autoscaler's `elastic-userdata.template` so elastic nodes can carry
the prereqs when Longhorn is enabled. Default-off keeps a merge a no-op.

> The prod **control-plane** node's `user_data` in `terraform/contabo/vps.tf` has
> `ignore_changes = [user_data]` and cloud-init only runs on first boot — so editing
> it does **not** touch the live node. The live control-plane's prereqs are installed
> via path (b), not by re-rendering cloud-init.

**(b) Existing/live nodes — Longhorn's official installation DaemonSets.** For the
already-running control-plane (and any live baseline node), Longhorn ships
`longhorn-iscsi-installation` and `longhorn-nfs-installation` DaemonSets that
install the packages in-place, no reboot. These are a **human-gated `kubectl apply`**
during the maintenance window (they are privileged; not auto-synced). This is the
correct path for the live control-plane because we never rebuild it just to add a
package. The environment-check job (`longhorn-environment-check.sh`, upstream) is
the read-only pre-flight to run first.

> **Read-only pre-flight (no mutation):** upstream `longhorn-environment-check.sh`
> reports per-node whether `open-iscsi`, `nfs-common`, and multipath state are OK.
> Run it before enabling anything; it only reads.

### 3.2 multipathd caveat

Ubuntu's `multipathd` can grab Longhorn devices and block attach. If present, add a
Longhorn blacklist to `/etc/multipath.conf` (`blacklist { devnode "^sd[a-z0-9]+" }`
scoped appropriately) or disable `multipathd`. Verify per node during the gated
rollout; codify into cloud-init only after confirming the base image's behavior.

---

## 4. Sizing, replica count, and the StorageClass

### 4.1 Replica count = 3

With ≥3 schedulable nodes (4 baseline + up to 6 elastic), **3 replicas** is the
standard HA choice: a volume survives losing any 2 replica nodes and can rebuild.
Set `defaultReplicaCount: 3` and `replicaSoftAntiAffinity: false` so the 3 replicas
land on **3 distinct nodes** — anti-affinity is what makes the count meaningful.

**Caveat — elastic nodes are ephemeral.** The 6 elastic nodes are autoscaled and
**billing-reaped** (released before renewal). Putting a DB replica on a node that
the autoscaler may delete is dangerous: Longhorn must rebuild the replica elsewhere
on every reap, causing churn and rebuild traffic. Therefore:

- **Pin Longhorn replicas to durable (baseline) nodes.** Use a Longhorn
  `nodeSelector` / `tags` scheme so replicas schedule only on the 4 baseline nodes,
  not on `fuzeinfra.io/pool=elastic` nodes. With 4 baseline nodes, 3 replicas fit
  with one node of headroom. (Implementation: label baseline nodes with a Longhorn
  disk tag and set the StorageClass `diskSelector`/`nodeSelector` accordingly, OR
  disable scheduling on elastic Longhorn nodes.) Confirm the exact baseline node
  count is ≥3 at rollout time (open question O-3).

### 4.2 Disk sizing on V92 (4 vCPU / 8 GB)

Current prod PVC footprint (from the S3 design inventory): Postgres 20Gi, ES 20Gi,
MongoDB 10Gi, Kafka 10Gi, Neo4j 5+1Gi, Loki 5Gi, ChromaDB 5Gi, Redis 2Gi, RabbitMQ
2Gi, Alertmanager 1Gi ≈ **~101Gi of PVCs**. With **3× replication**, the *replicated*
DB subset (Postgres+Mongo+Neo4j+Redis ≈ 38Gi) consumes **~114Gi of raw disk spread
across the baseline nodes** (38Gi × 3). Only the DBs we choose to migrate get
replicated — not everything at once.

- Set `storageMinimalAvailablePercentage: 15` and
  `storageOverProvisioningPercentage: 100` (no thin over-provisioning on small
  disks — avoid a node filling up and evicting kubelet).
- Confirm each baseline node's `/var/lib/longhorn` filesystem has headroom for its
  share of replicas **before** migrating a DB (open question O-4). V92 default disk
  is modest; a dedicated data volume per baseline node is the durable answer if the
  root disk is tight.
- Longhorn's own footprint: `longhorn-manager` + `instance-manager` per node
  (~request 100–200Mi each). Budget it against the already-tight control-plane;
  this is another reason to keep DB pods (and their Longhorn replicas) **off** the
  saturated control-plane once migrated — which is the whole point of item A.

### 4.3 The `longhorn` StorageClass — opt-in, NOT default

**Keep `local-path` the cluster default.** Ship a **non-default** `longhorn`
StorageClass so nothing uses Longhorn until a PVC explicitly names it:

```yaml
apiVersion: storage.k8s.io/v1
kind: StorageClass
metadata:
  name: longhorn
  annotations:
    storageclass.kubernetes.io/is-default-class: "false"   # local-path stays default
provisioner: driver.longhorn.io
allowVolumeExpansion: true
reclaimPolicy: Retain            # never auto-delete a DB volume on PVC delete
volumeBindingMode: Immediate
parameters:
  numberOfReplicas: "3"
  staleReplicaTimeout: "30"
  fromBackup: ""
  fsType: "ext4"
```

This is delivered as a **gated template in the umbrella chart**
(`helm/fuzeinfra/templates/storageclass-longhorn.yaml`, rendered only when
`longhorn.storageClass.enabled=true`, default false) so it:
- rides the existing `fuzeinfra-prod` Argo sync + `helm-validate.yml` +
  `kubeconform` gate (no new pipeline),
- stays default-OFF (a merge renders nothing),
- and is **decoupled** from the Longhorn controller install (the SC is inert until
  `driver.longhorn.io` exists, so ordering is forgiving — but a human enables the
  controller Application first).

`reclaimPolicy: Retain` is deliberate: a DB PVC deletion must never silently reap
the underlying replicated volume.

---

## 5. What is gated OFF (so a merge is a prod no-op)

| Artifact | Gate | Effect of merging |
|---|---|---|
| `argocd/applications/longhorn.yaml` | **Manual sync** (no `automated`); the Application isn't even created until a human `kubectl apply`s it | Nothing. File sits in git. |
| `helm/fuzeinfra/templates/storageclass-longhorn.yaml` | `if .Values.longhorn.storageClass.enabled` — default **false** | Renders nothing in any overlay. |
| `longhorn:` block in `values.yaml` / overlays | `enabled: false`, `storageClass.enabled: false` | Inert values. |
| `enable_longhorn_prereqs` in `modules/contabo-k3s-node` | bool, default **false** | Cloud-init unchanged for existing consumers; new nodes get no extra packages. |
| elastic-userdata prereq block | commented-out, opt-in | Base64-embedded template unchanged until an operator re-encodes it. |

No existing PVC, StatefulSet, or `global.storageClass` is changed. `local-path`
remains the default. The DBs keep running exactly where they are.

---

## 6. Per-DB migration plan (human-gated, per maintenance window — NOT executed here)

Each database moves off `local-path` onto a `longhorn` PVC in its **own** scheduled,
reversible maintenance window. **General pattern** (backup → restore onto a fresh
Longhorn PVC, the safe path — a live block-clone/`pv-migrate` is the faster
alternative but riskier and is the fallback, not the default):

1. Confirm the **S3 backup CronJob** for that DB (already shipped, #377) has a
   recent good backup in `fuzeinfra-backups` — this is the rollback floor.
2. Quiesce writers (scale the DB's dependents to 0 / put in maintenance).
3. Take a fresh logical dump with the native tool.
4. Create the new PVC with `storageClassName: longhorn` (RWO), let Longhorn
   provision + place the 3 replicas.
5. Restore the dump into the new volume, verify row/collection counts + a smoke
   query.
6. Flip the StatefulSet's PVC/`storageClassName` to the Longhorn PVC (a values
   change, committed → Argo sync — **never** a live `kubectl edit`; StatefulSet PVC
   templates are immutable, so this is a delete-STS-keep-PVC + re-apply dance, done
   via git).
7. Unquiesce, watch, keep the old `local-path` PVC (Retain) until confidence, then
   reclaim.

Per-DB specifics:

| DB | Dump / restore tool | Notes |
|---|---|---|
| **Postgres** | `pg_dumpall` → `psql` restore (or `pg_dump -Fc` + `pg_restore`) | Largest (20Gi); longest window. RWO. |
| **MongoDB** | `mongodump --archive --gzip` → `mongorestore` | RWO. |
| **Neo4j** | `neo4j-admin database dump` → `neo4j-admin database load` (offline on Community) | Requires brief offline dump; schedule off-hours. RWO. |
| **Redis** | copy `dump.rdb` onto the new PVC, or `BGSAVE` → restore | Lowest value (cache/broker); can even be recreated empty. RWO. |

**Do Postgres/Mongo/Neo4j first** (irreplaceable data), Redis last (or skip —
re-provision empty). Migrate **one DB per window**, verify, then the next — never
all four at once (the control-plane can't take the simultaneous churn, and a
single-window rollback is cleaner).

**Alternative — `pv-migrate` / volume-clone:** `pv-migrate` (rsync between PVCs) or a
Longhorn snapshot→restore avoids a logical dump for large volumes, but does a
block/file copy of live data. Use it only for stores where a consistent logical dump
is impractical, and still inside a quiesced window. Backup→restore stays the default
because it also validates the backup.

---

## 7. Interaction with the private-VLAN migration (companion doc)

Longhorn replicas continuously sync over the network (replica rebuild + live
writes). On the current **public-IP + Flannel VXLAN** overlay, that replication
traffic rides the public NIC — bandwidth-metered and less private. Once the
**private-VLAN migration** (companion doc §3) moves node/overlay traffic onto
`eth1` (`10.0.0.0/22`), **Longhorn replication automatically rides the private
network** because it uses the pod/overlay network, not a separate transport. So the
correct sequencing is:

1. Land Longhorn prereqs + controller (this doc), migrate **one** low-risk volume to
   validate, while still single-ish node.
2. Bring baseline workers onto the **private VLAN** (companion doc P4/P5).
3. Then migrate the DBs to Longhorn with 3 replicas spread across private-networked
   baseline nodes — now the heavy replication traffic is on `eth1`, not the public
   NIC.

Doing Longhorn-migrate-DBs **before** the private VLAN would push replication over
the public overlay; doing the VLAN first makes the DB migration's replication cheap
and private. **Recommended order: private-VLAN first (or concurrently), DB-to-Longhorn
migration after the VLAN is live.**

## 8. How this unblocks control-plane HA

Once a DB's data is a **3-replica Longhorn volume** spread across baseline nodes, the
DB pod is no longer welded to `vmi3383846`:

- **Item A (spread DBs off the hot node):** reschedule the DB pod to a baseline
  worker; Longhorn attaches the volume there from a surviving replica. The
  control-plane's ~84% memory pressure drops.
- **Control-plane reinstall / private-VLAN re-IP:** the control-plane can be
  rebooted / reinstalled / re-IPed without taking irreplaceable data down with it —
  the volume lives on other nodes' replicas.
- **Control-plane HA (multi-server k3s):** true HA requires both an HA datastore
  (embedded etcd across ≥3 servers) **and** node-independent storage for stateful
  workloads. Longhorn provides the latter; without it, adding control-plane nodes
  is pointless because the DBs can't follow. Longhorn is the **precondition**, not
  the whole of HA — the etcd/`--cluster-init` server topology is a separate,
  later gated change.

---

## 9. Phased rollout (every step human-gated)

Each phase is a separate PR / gated action. **This design doc (P0) changes nothing.**

| Phase | Contents | Human gate |
|---|---|---|
| **P0 (this doc)** | `docs/design/longhorn-storage.md` | Review + merge. No infra change. |
| **P1 — gated scaffolding (impl PR)** | `argocd/applications/longhorn.yaml` (manual sync), `templates/storageclass-longhorn.yaml` (gated), `longhorn:` values (OFF), `enable_longhorn_prereqs` in the node module + elastic template (OFF) | Merge is a **no-op**. Nothing deploys. |
| **P2 — node prereqs** | Enable `enable_longhorn_prereqs` for new baseline nodes; `kubectl apply` Longhorn's iscsi/nfs installation DaemonSets on live nodes | Run `longhorn-environment-check.sh` first (read-only). Human applies in a window. |
| **P3 — controller install** | `kubectl apply -f argocd/applications/longhorn.yaml`, then **manually Sync** in Argo | Human verifies all engine/manager pods healthy; UI reachable (behind CF Access). |
| **P4 — enable StorageClass** | Set `longhorn.storageClass.enabled=true` in `values-contabo.yaml` | Merge → Argo syncs the non-default `longhorn` SC. `local-path` stays default. |
| **P5 — per-DB migration** | One DB per window (§6), values-driven PVC flip via git | Scheduled, reversible, backup-verified. One DB at a time. |
| **P6 — turn on Argo auto-sync** | Add `automated{prune,selfHeal}` to `longhorn.yaml` | Only after P3–P5 proven stable. |

---

## 10. Open questions (carry into the gated rollout, not blockers for this doc)

1. **O-1 Longhorn version pin** — confirm the exact `targetRevision` against the
   live k3s version + Kubernetes API version at rollout (Longhorn ↔ k8s compat
   matrix). Pin the patch, bump deliberately.
2. **O-2 UI exposure** — the Longhorn UI is an admin surface; if exposed it needs a
   CF Access app + App Launcher entry (per CLAUDE.md ingress rule) and a Traefik
   Ingress. Default: keep the UI ClusterIP-only, port-forward for ops.
3. **O-3 Baseline node count** — confirm ≥3 **durable (non-elastic)** schedulable
   nodes exist for 3-replica anti-affinity; if only the control-plane is durable
   today, the replica-count / node-tag scheme must be adjusted before migration.
4. **O-4 Disk headroom** — measure each baseline node's `/var/lib/longhorn`
   filesystem free space vs. its replica share; consider a dedicated data volume
   per baseline node if the root disk is tight on V92.
5. **O-5 multipathd** — confirm the Contabo Ubuntu base image's `multipathd`
   behavior; add the Longhorn blacklist to cloud-init only after confirming.
6. **O-6 Elastic exclusion for replicas** — finalize the node-tag / nodeSelector
   scheme that keeps Longhorn replicas off billing-reaped elastic nodes.
7. **O-7 RWX need** — confirm whether any workload needs ReadWriteMany (drives the
   `nfs-common` + share-manager requirement); RWO-only can skip NFS but we install
   `nfs-common` anyway for forward-compat.

---

## 11. What this design explicitly does NOT do

- Does not `helm install` Longhorn, `kubectl apply` any manifest, or sync any Argo app.
- Does not migrate any database or `kubectl`-mutate prod.
- Does not change `global.storageClass`, any existing PVC, or any StatefulSet.
- Does not make `longhorn` the default StorageClass (`local-path` stays default).
- Does not touch `terraform/contabo` and adds no instance enumeration — the
  **ELASTIC-EXCLUSION invariant is preserved** (only the reusable node module +
  the elastic userdata template gain a default-off prereq toggle).
- Does not commit any secret.
</content>
</invoke>
