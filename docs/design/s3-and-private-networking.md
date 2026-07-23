# Design: Contabo Object Storage (S3) + Private Networking for FuzeInfra prod

Status: **Proposed (design only)** — no `terraform apply`, no prod mutation, no data
migration is performed by this document. Every apply/migration step below is
explicitly **gated on human review**.

Author: design synthesis (Claude, session `bfebd23d-f1a8-49d4-ad24-522a1d169855`)
Date: 2026-07-23
Scope: `terraform/contabo/`, `modules/contabo-k3s-node/`, `helm/fuzeinfra/`,
`deploy/sealed-secrets/`.

---

## 0. Executive summary (the honest version)

This design does **two orthogonal things** that are often (wrongly) lumped together:

1. **Object Storage (S3).** Provision Contabo Object Storage — an instance +
   bucket(s) — via the already-configured `contabo/contabo` Terraform provider,
   deliver its S3 credentials as a **SealedSecret** (never `kubectl create secret`),
   and then use S3 only for the workloads that can genuinely use it: **Loki**
   (native object-store backend, already fully coded in the chart), **scheduled
   DB backups** (dump → bucket via CronJobs), and **app/blob artifacts**. Object
   Storage rides the **public S3 endpoint** (`eu2.contabostorage.com`) and is
   **not** attached to the private network.

2. **Private networking.** Codify the already-created Contabo private network
   (id `60932`, CIDR `10.0.0.0/22`, DC "European Union 2") in Terraform, attach the
   explicitly-named control-plane node, and (human-gated) move the k3s node/overlay
   traffic onto `eth1`. The **per-instance VPC "Private Networking" add-on is a
   manual panel purchase** — the API returns HTTP 402 without it and the
   `contabo/contabo` provider cannot buy it — so a human clicks that for existing
   nodes before any attach can succeed. **Operational rollout (decision
   2026-07-23): the 3 baseline workers + all elastics go on the VLAN FIRST; the
   control-plane `vmi3383846` is deferred behind A (Longhorn) + B (HA)** because
   its reinstall would wipe the Postgres/Redis data. **New elastic nodes get the
   add-on automatically via the provider's create-time `addOns`.**

**The one dishonest framing we refuse:** "move all stateful data to S3." S3 is an
HTTP object store — no POSIX filesystem, no block writes, no `fsync`/`mmap`/locking.
Postgres, MongoDB, Redis, Neo4j, Elasticsearch, ChromaDB, Kafka, and RabbitMQ
**must keep running on local-path block PVCs**. Their only S3 story is
**backup/snapshot offload**, not live storage. So the real, feasible scope is
"**logs + backups + blobs on S3; databases stay on block storage, protected by
scheduled S3 backups**."

---

## 1. Current state (verified against the repo)

| Fact | Source |
|---|---|
| Prod is Contabo single-node k3s, ns `fuzeinfra`, Argo CD `automated{prune,selfHeal}`, ServerSideApply | `argocd/applications/fuzeinfra-prod.yaml` |
| Terraform in `terraform/contabo/` manages **exactly one node** (`contabo_instance.prod`) and NEVER enumerates the account instance list (ELASTIC-EXCLUSION invariant) | `terraform/contabo/main.tf` L23-42 |
| Contabo provider already configured with OAuth2 (`contabo/contabo`, `~> 0.1`) — same block object-storage + private-network resources use | `terraform/contabo/main.tf` L47-83 |
| State backend is S3 + DynamoDB lock, partial config supplied at init | `terraform/contabo/backend.tf` |
| Storage is **local-path (RWO block) only**; ~101Gi across 12 PVCs; no object store, no MinIO, no blob data | `helm/fuzeinfra/values-contabo.yaml`, `templates/databases.yaml` |
| Loki S3 backend is **fully coded** in the chart, `enabled:false`; the commented recipe in `values-contabo.yaml` uses `kubectl create secret` (violates GitOps) | `values.yaml` L338-363, `configmaps-monitoring.yaml` L235-285, `values-contabo.yaml` L94-108 |
| No backup CronJobs exist anywhere in the chart | grep: none |
| SealedSecrets is the credential-delivery standard: seal **offline** against the published public cert with `scripts/seal-secret.sh`, commit encrypted YAML, Argo syncs; controller is its own Argo app | `scripts/seal-secret.sh`, `argocd/applications/sealed-secrets.yaml`, `deploy/sealed-secrets/*.yaml` |
| ES + RabbitMQ + Airflow scaled to 0 for node headroom (#93); PVCs retained | `values-contabo.yaml` |
| Private network `60932` (10.0.0.0/22, "European Union 2") already created live via API; VPC add-on per-instance is a manual panel purchase (HTTP 402 without it) | given / Contabo panel |

---

## 2. Object Storage via Terraform

### 2.1 Provider resources (no new provider, no new credentials)

The pinned `contabo/contabo ~> 0.1` (>= 0.1.42) provider exposes both resources on
the **same OAuth2 provider block** already in `main.tf`:

- `contabo_object_storage` — provisions the S3 tenant (purchased quota).
  Required: `region` (`EU` | `US-central` | `SIN`), `total_purchased_space_tb`.
  Optional: `display_name`, `auto_scaling { state, size_limit_tb }`.
  Computed: `id`, `s3_url`, `s3_tenant_id`, `tenant_id`, `data_center`, `status`.
- `contabo_object_storage_bucket` — creates a bucket **via the Contabo OAuth2 API**
  (not the S3 API, so no S3 keys needed at plan time).
  Required: `name`, `object_storage_id`. Optional: `public_sharing` (default false).

> This introduces **no `data "contabo_instance"`** and no `for_each` over the
> account instance inventory, so it does **not** weaken the ELASTIC-EXCLUSION
> invariant in `main.tf`. It is a new, self-contained resource pair.

### 2.2 New file `terraform/contabo/object-storage.tf` (gated OFF by default)

```hcl
# Applying contabo_object_storage PURCHASES paid storage (~€6.99/mo per 1 TB).
# Gated behind var.enable_object_storage (default false) so a routine apply of
# this directory never buys storage. Enable only in a human-reviewed apply.
resource "contabo_object_storage" "loki" {
  count                    = var.enable_object_storage ? 1 : 0
  region                   = "EU"            # -> eu2.contabostorage.com, co-located with the EU2 node
  total_purchased_space_tb = var.object_storage_purchased_tb  # default 0.25 (smallest tier)
  display_name             = "fuzeinfra-storage"
  # auto_scaling { state = "enabled"  size_limit_tb = 1 }   # optional growth guardrail
}

resource "contabo_object_storage_bucket" "loki" {
  count             = var.enable_object_storage ? 1 : 0
  name              = "fuzeinfra-loki"
  object_storage_id = contabo_object_storage.loki[0].id
  public_sharing    = false
}

resource "contabo_object_storage_bucket" "backups" {
  count             = var.enable_object_storage ? 1 : 0
  name              = "fuzeinfra-backups"
  object_storage_id = contabo_object_storage.loki[0].id
  public_sharing    = false
}

resource "contabo_object_storage_bucket" "blobs" {
  count             = var.enable_object_storage ? 1 : 0
  name              = "fuzeinfra-blobs"
  object_storage_id = contabo_object_storage.loki[0].id
  public_sharing    = false
}
```

**Bucket topology:** three purpose-scoped buckets in one tenant
(`fuzeinfra-loki`, `fuzeinfra-backups`, `fuzeinfra-blobs`). One tenant keeps the
single account-level credential pair simple; separate buckets keep lifecycle rules
and access reasoning clean. (Open question O-2 revisits shared-bucket + prefixes.)

### 2.3 Variables (`variables.tf` additions)

```hcl
variable "enable_object_storage" {
  description = "Provision Contabo Object Storage (PAID). Default off so a routine apply never buys storage."
  type        = bool
  default     = false
}
variable "object_storage_purchased_tb" {
  description = "Purchased quota in TB. Smallest tier ~0.25; confirm the provider accepts sub-1TB (open question O-5)."
  type        = number
  default     = 0.25
}
```

### 2.4 Outputs (`outputs.tf` additions) — read the endpoint from state, never hardcode

```hcl
output "object_storage_id" {
  value = var.enable_object_storage ? contabo_object_storage.loki[0].id : ""
}
output "object_storage_s3_url" {
  description = "Full S3 endpoint incl. scheme (strip scheme/port to get the bare host the Loki chart wants)."
  value       = var.enable_object_storage ? contabo_object_storage.loki[0].s3_url : ""
}
output "object_storage_buckets" {
  value = var.enable_object_storage ? [
    contabo_object_storage_bucket.loki[0].name,
    contabo_object_storage_bucket.backups[0].name,
    contabo_object_storage_bucket.blobs[0].name,
  ] : []
}
```

### 2.5 S3 access key + secret — a manual panel step, then SealedSecret

The S3 access key/secret are **account-level credentials**, NOT produced by any
Terraform resource. A human fetches them once from the panel
(**Account → Security & Access → S3 Object Storage Credentials**) — one pair per
account, shared across all buckets. They then seal them **offline** (no cluster
access) with the repo's canonical tool and commit the encrypted manifest:

```bash
# fetched from the Contabo panel, never echoed into shell history in a shared env
scripts/seal-secret.sh fuzeinfra/loki-s3 \
  LOKI_S3_ACCESS_KEY_ID=<key> \
  LOKI_S3_SECRET_ACCESS_KEY=<secret> \
  > deploy/sealed-secrets/loki-s3-credentials.yaml
```

This yields a `deploy/sealed-secrets/loki-s3-credentials.yaml` shaped exactly like
the existing `deploy/sealed-secrets/fuzesales-db-credentials.yaml` (SealedSecret,
ns `fuzeinfra`, name `loki-s3`), which Argo reconciles into the `loki-s3` Secret.
**This replaces the `kubectl create secret` line in the current `values-contabo.yaml`
comment** — hand-created secrets get orphaned and contradict the no-hand-mutate rule.

> The **same account credential pair** covers the backups and blobs buckets. A
> second SealedSecret (`fuzeinfra/objstore-s3` with generic `S3_ACCESS_KEY_ID` /
> `S3_SECRET_ACCESS_KEY`) is sealed the same way for the backup CronJobs (§4.2).

---

## 3. Private networking via Terraform

### 3.1 What the provider can and cannot do

- **Cannot:** buy the per-instance **"Private Networking" VPC add-on**. That is a
  paid panel purchase; the Contabo API returns **HTTP 402** when you try to attach
  an instance that lacks the add-on, and `contabo/contabo` has no resource for the
  purchase. **A human buys the add-on for the control-plane node in the panel
  first.** This is exactly the same manual-gate class as the object-storage
  purchase — it is not the automation's job.
- **Can:** import the already-created private network `60932` and attach the
  explicitly-named control-plane instance once the add-on exists.

### 3.2 New file `terraform/contabo/private-network.tf`

```hcl
# The private network (id 60932, 10.0.0.0/22, DC "European Union 2") was created
# live via the Contabo API. Import it — do NOT recreate:
#   terraform import contabo_private_network.fuzeinfra 60932
#
# PREREQUISITE (manual, human): the per-instance "Private Networking" add-on must
# be purchased in the Contabo panel for the control-plane node, or attach returns
# HTTP 402. Terraform CANNOT buy the add-on.
resource "contabo_private_network" "fuzeinfra" {
  count       = var.enable_private_network ? 1 : 0
  name        = "fuzeinfra-prod"
  region      = "EU"                 # must match DC "European Union 2"
  description = "FuzeInfra prod k3s private overlay (CIDR 10.0.0.0/22)"

  # Attach ONLY the explicitly-named control-plane node. This references a single
  # named resource id — it is NOT an enumeration of the account instance list, so
  # the ELASTIC-EXCLUSION invariant in main.tf is preserved. Elastic/consumer
  # worker nodes are attached out-of-band by their own provisioning path
  # (modules/contabo-k3s-node), never here.
  instance_ids = [contabo_instance.prod.id]

  lifecycle {
    # Contabo may reorder/normalize instance_ids; avoid perpetual diffs.
    ignore_changes = [instance_ids, description]  # Contabo reorders instance_ids; guard the elastic-exclusion invariant + avoid perpetual diffs
  }
}
```

```hcl
# variables.tf
variable "enable_private_network" {
  description = "Attach the control-plane node to private network 60932. Requires the manual VPC add-on purchase first."
  type        = bool
  default     = false
}
```

### 3.3 OS-level: `eth1` + cloud-init (in `modules/contabo-k3s-node/` and the prod node)

When the VPC add-on is active, Contabo presents a second NIC (`eth1`) on the
attached instance. Cloud-init assigns a static private address on it (netplan /
systemd-networkd). This belongs in the **reusable worker module**
(`modules/contabo-k3s-node/`) so every baseline node that joins the private
network gets the same treatment, and is mirrored into the control-plane node's
`user_data` in `vps.tf`:

```yaml
#cloud-config
write_files:
  - path: /etc/netplan/60-fuzeinfra-privnet.yaml
    content: |
      network:
        version: 2
        ethernets:
          eth1:
            addresses: [ "10.0.0.10/22" ]   # control-plane; workers get 10.0.0.11+
            # No gateway on eth1 — the private net is intra-cluster only; the
            # default route stays on eth0 (public) for egress + the CF tunnel.
runcmd:
  - netplan apply
  # Firewall: allow the k3s API + VXLAN overlay on the PRIVATE subnet only.
  - ufw allow from 10.0.0.0/22 to any port 6443 proto tcp
  - ufw allow from 10.0.0.0/22 to any port 8472 proto udp   # Flannel VXLAN
  - ufw allow from 10.0.0.0/22 to any port 10250 proto tcp  # kubelet metrics (private only)
```

> IP assignment note: `10.0.0.10` for the control-plane and `10.0.0.11+` for
> workers is a **static, human-assigned** scheme (no DHCP assumption). If Contabo's
> VPC hands out DHCP on `eth1`, switch `addresses:` to `dhcp4: true` and read the
> lease — confirm during the gated rollout (open question O-6).

### 3.4 k3s `config.yaml` — move node + overlay traffic onto `eth1`

Today the k3s server and workers run the Flannel VXLAN overlay over the **public
IP** (see `vps.tf` / `provisioning.tf` comments: "durable fix: … private VLAN").
Private networking is that durable fix. On each node write
`/etc/rancher/k3s/config.yaml`:

```yaml
# control-plane (server)
node-ip: "10.0.0.10"
flannel-iface: "eth1"
tls-san:
  - "10.0.0.10"        # keep the public tls-san too during migration
```

```yaml
# worker (agent) — set by modules/contabo-k3s-node
node-ip: "10.0.0.11"   # its own private address
flannel-iface: "eth1"
```

Once the overlay runs on `eth1`, the public-Internet `ufw allow 8472/udp Anywhere`
bootstrap rule in `provisioning.tf` can be tightened to `from 10.0.0.0/22`,
removing the current "opened Anywhere as a bootstrap default" risk the comments
already flag.

### 3.5 Honest caveat: value vs. disruption

On a **single-node** cluster private networking has near-zero immediate benefit —
there is no second node to talk to over `eth1`. Its real payoff is when
**baseline/elastic worker nodes** join over the private subnet instead of
public-IP+VXLAN. And changing a **running** node's `flannel-iface`/`node-ip` is
**disruptive**: it re-IPs the node, forcing pod-network churn and effectively a
node rejoin (kube-proxy/CoreDNS/Traefik re-plumb). Therefore:

- Importing the network + attaching the node (§3.2) is low-risk and can land first.
- Flipping `flannel-iface`/`node-ip` on the live control-plane is **human-gated,
  scheduled, and reversible** (keep the public `tls-san`; be ready to roll back
  `config.yaml` and reboot). Do it when the first private-net worker is ready to
  join, so the migration and the payoff happen together.

---

## 4. The honest stateful-data → S3 plan

Classification of every stateful store (from the inventory pass):

| Store | Prod PVC | Class | S3 story |
|---|---|---|---|
| **Loki** | 5Gi | **B — native S3** | Flip `loki.s3.enabled`; chart already renders it. Highest ROI. |
| **Prometheus** | 20Gi | B — S3 only via Thanos/Mimir | **Deferred + documented.** Needs a new stateful component the saturated node can't host yet. |
| Postgres | 20Gi | **A — block only** | `pg_dump`/`pg_dumpall` → `fuzeinfra-backups` (CronJob). |
| MongoDB | 10Gi | A | `mongodump --archive --gzip` → bucket. |
| Neo4j | 5Gi+1Gi | A | `neo4j-admin database dump` (offline on Community) → bucket. |
| Elasticsearch | 20Gi (0 pods #93) | A | `repository-s3` snapshot API — only when restored from 0. |
| ChromaDB | 5Gi | A | tar the persist dir → bucket. |
| Redis | 2Gi | A | RDB copy → bucket (low value; broker/cache). |
| Kafka | 10Gi | A | no S3 tiering on this cp-kafka 7.6.1 single-node; segments stay on block. |
| RabbitMQ | 2Gi (0 pods #93) | A | Mnesia stays on block; optional definitions export. |
| Alertmanager | 1Gi | A | tiny operational state; not S3 material. |
| Grafana | none (emptyDir) | — | stateless; provisioned from ConfigMaps. Nothing to move. |
| Airflow | none | — | metadata in Postgres, broker in Redis; no own PVC. |

**Databases keep running on block storage. Full stop.** What moves to S3 is: Loki's
chunks/index (live), point-in-time DB **backups**, and **app blobs**.

### 4.1 Loki → native S3 (config-only, already coded)

In `helm/fuzeinfra/values-contabo.yaml`, replace the commented `kubectl create
secret` recipe with the real enablement (the chart template already supports every
field — no template change):

```yaml
loki:
  s3:
    enabled: true
    endpoint: "eu2.contabostorage.com"   # bare host, scheme stripped from s3_url
    region: "default"                     # Contabo ignores AWS regions; must be non-empty
    bucket: "fuzeinfra-loki"
    s3ForcePathStyle: true                # mandatory: Contabo is path-style
    insecure: false                       # HTTPS
    existingSecret: "loki-s3"             # the SealedSecret from §2.5
```

Moving 5Gi of append-only logs off the saturated node's disk directly helps #93.
Logs tolerate S3 latency and are the single cleanest candidate. **Verify before
flipping** (open question O-1): `aws s3api head-bucket --endpoint-url
https://eu2.contabostorage.com --bucket fuzeinfra-loki` (or `mc ls`) to confirm
plain endpoint+bucket addressing works and no tenant-id path prefix is required.

### 4.2 DB backups → S3 (new gated chart template `templates/backups.yaml`)

Databases stay on their local-path PVCs; a new **gated** template renders one
CronJob per store behind `backups.<svc>.enabled`, sharing a `backups.s3` block
(same shape as `loki.s3`):

```yaml
# values.yaml (defaults, all OFF)
backups:
  s3:
    endpoint: "eu2.contabostorage.com"
    region: "default"
    bucket: "fuzeinfra-backups"
    s3ForcePathStyle: true
    existingSecret: "objstore-s3"   # SealedSecret with S3_ACCESS_KEY_ID/_SECRET
  postgres: { enabled: false, schedule: "0 1 * * *", retentionDays: 14 }
  mongodb:  { enabled: false, schedule: "0 2 * * *", retentionDays: 14 }
  neo4j:    { enabled: false, schedule: "0 3 * * *", retentionDays: 14 }
```

Each CronJob: reads DB creds from the existing `fuzeinfra-secrets` and S3 creds
from the SealedSecret, runs the native tool, streams to `aws s3 cp - s3://…/<svc>/<ts>`
(or `mc`/`rclone`). Per-tool: Postgres `pg_dumpall`; MongoDB `mongodump --archive
--gzip`; Neo4j `neo4j-admin database dump` (Community = brief offline dump, run
off-hours — open question O-5/edition). **Stagger schedules** (01:00/02:00/03:00)
so backups never hit the saturated node at once. Retention is enforced by an **S3
lifecycle rule** expiring old objects (CronJobs alone grow unbounded). Enable
Postgres/Mongo/Neo4j first (highest-value data); ES/Chroma when those services are
restored from 0.

### 4.3 App blobs → S3 (a bucket + client config, not a FuzeInfra service change)

FuzeInfra is infra-only. Blob storage for apps = the `fuzeinfra-blobs` bucket +
scoped credentials exposed to consumers the same way `DATABASE_URL` is (env /
SealedSecret + docs in the project-integration guide): endpoint
`eu2.contabostorage.com`, `region=default`, **path-style**, an S3 SDK. FuzeInfra
runs no blob service itself (a MinIO gateway is optional for local/dev parity only;
prod points at Contabo Object Storage).

### 4.4 Prometheus — deferred, documented

Keep Prometheus on its 20Gi local TSDB. Long-term-to-S3 requires introducing
**Thanos** (sidecar upload + store gateway + compactor) or `remote_write` to
Thanos-receive/Mimir — a new stateful component the single saturated node cannot
spare today (#93 already zeroed three services). Documented future path: Thanos
sidecar → S3 once a second node exists. Not in this scope.

---

## 5. Phased rollout (every apply/migration human-gated)

Each phase is a **separate PR**. Nothing here runs `terraform apply`, mutates prod,
or migrates data as part of this design doc.

| Phase | PR contents | Human gate before it takes effect |
|---|---|---|
| **P0 (this doc)** | `docs/design/s3-and-private-networking.md` | Review + merge. No infra change. |
| **P1 — Object Storage TF** | `object-storage.tf`, `variables.tf`, `outputs.tf` (all gated OFF) | Reviewer sets `enable_object_storage=true` and runs the human-reviewed `terraform apply` (PAID). Then fetch S3 keys from panel. |
| **P2 — Credentials** | `deploy/sealed-secrets/loki-s3-credentials.yaml` + `objstore-s3` (sealed offline) | Sealing is offline; merge → Argo syncs the Secret. No plaintext ever committed. |
| **P3 — Loki S3 flip** | `values-contabo.yaml` `loki.s3` enablement | **Smoke-test the bucket first** (§4.1 O-1). Merge → Argo enables Loki S3; logs move off disk (helps #93). |
| **P4 — Private network TF** | `private-network.tf` + `enable_private_network` var (OFF) | Human buys the VPC add-on in the panel, imports net 60932, sets the flag, runs the reviewed apply. This attaches ONLY the control-plane and is gated on **A+B** (below). |
| **P4a — Workers + elastics on the VLAN (FIRST)** | `modules/contabo-k3s-node` eth1/`flannel-iface` cloud-init (workers) + provider create-time `addOns` (elastics) | Human buys the add-on per existing worker/elastic in the panel; then the automated per-node reinstall-onto-VLAN runs (drain → assign → reinstall → verify), one node at a time. New elastics come up on the VLAN automatically. **This is the operational first step; the control-plane (P4/P5) waits for A+B.** |
| **P5 — Node/overlay migration** | `eth1` cloud-init + k3s `config.yaml` (`flannel-iface: eth1`) in `modules/contabo-k3s-node` + `vps.tf` | **Scheduled, reversible, disruptive.** Do it when the first private-net worker is ready to join. Keep public `tls-san`; rollback plan ready. |
| **P6 — DB backups** | `templates/backups.yaml` + `backups.*` values (OFF) | Enable Postgres/Mongo/Neo4j after the `fuzeinfra-backups` bucket + `objstore-s3` secret exist; add the S3 lifecycle retention rule. |
| **P7 — Blobs + Thanos (future)** | integration-guide docs for `fuzeinfra-blobs`; Prometheus/Thanos scoped separately | On demand / when a second node exists. |

**Prod-is-GitOps reminder:** never `kubectl patch`/`edit` a live prod resource
(Argo `selfHeal` reverts it). Every prod change lands via Git → Argo. Terraform
applies (P1, P4, P5) are the human-reviewed merge-to-apply path, never hand-run
against prod out-of-band.

---

## 6. Open questions (carry into the gated rollout, not blockers for this doc)

1. **O-1 Loki bucket addressing** — confirm plain `endpoint`+`bucket` works vs. a
   required `eu2.contabostorage.com/<tenant-id>:<bucket>` path prefix
   (`s3_tenant_id` is documented as "public-sharing only"). Smoke-test with
   `aws s3api head-bucket`/`mc ls` **before** flipping `enabled:true`.
2. **O-2 Bucket topology** — three buckets in one tenant (chosen) vs. one shared
   bucket with prefixes. Affects credential scoping + lifecycle rules.
3. **O-3 SealedSecrets controller** — confirm (read-only) the `sealed-secrets`
   Argo app/controller is actually healthy in prod before relying on it for P2.
4. **O-4 `region:"default"`** — confirm Loki 3.2.0's S3 client accepts the
   non-empty `"default"` (AWS SDK sometimes wants a real region like `us-east-1`).
5. **O-5 Provider specifics** — confirm the lockfile resolves a build with both
   object-storage resources; confirm `total_purchased_space_tb=0.25` is accepted
   vs. a 1TB floor; confirm `s3_url` scheme-stripping. `terraform providers
   schema -json` (read-only, no apply) answers all three.
6. **O-6 `eth1` addressing + Neo4j edition** — DHCP vs. static on the VPC NIC; and
   Neo4j Community (offline dump) vs. Enterprise (online backup).
7. **O-7 #93 services** — wire ES/RabbitMQ/Airflow backups now (disabled) and
   enable on restore, or defer entirely.

---

## 7. What this design explicitly does NOT do

- Does not run `terraform apply` or buy any paid product (object storage or VPC add-on).
- Does not migrate any prod data or `kubectl`-mutate prod.
- Does not move any database onto S3 (impossible; they stay on block storage).
- Does not weaken the ELASTIC-EXCLUSION invariant (no instance enumeration added).
- Does not commit any plaintext secret (all credentials via offline-sealed SealedSecrets).
