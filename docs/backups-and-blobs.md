# Stateful data on S3 (Contabo Object Storage)

FuzeInfra runs its stateful stores on **block / local-path** storage. Object
storage (S3-compatible, Contabo Object Storage) is **not** a live data volume for
any database — S3 has no POSIX/block semantics, so Postgres, MongoDB, Redis,
Neo4j, Elasticsearch, ChromaDB, Kafka and RabbitMQ all keep their data on the
node's block storage. S3 has exactly four roles on this platform:

| Use | Bucket | Mechanism | Default |
|-----|--------|-----------|---------|
| Loki log chunks + index | `fuzeinfra-loki` | Loki native S3 backend | **on** (prod, since 2026-09-01) |
| Scheduled DB backup dumps | `fuzeinfra-backups`, `db/` | backup CronJobs (this chart) | off |
| Prometheus TSDB backup | `fuzeinfra-backups`, `volumes/` | TSDB-snapshot CronJob (this chart) | off |
| Application blob storage | `fuzeinfra-blobs` | app SDK (S3 client) | n/a |

Everything is **default-disabled** and every enable step is human-gated and
GitOps-driven. Prometheus long-term storage (Thanos) is a separate, deferred
concern (documented at the bottom) — §4 is a nightly disaster-recovery floor,
not continuous durability.

**Coverage, so it is auditable at a glance.** Postgres, MongoDB, MariaDB and
Neo4j are covered by §2. The Prometheus TSDB is covered by §4 — it was **not**
covered before 2026-09-07, which is exactly why the metrics history is gone.
Loki needs no backup job while `loki.s3.enabled` is true: chunks and index go
straight to `fuzeinfra-loki`, and `fuzeinfra-loki-data` holds only working set
(WAL, tsdb-shipper cache, compactor working dir), whose loss costs the minutes
not yet shipped. Redis, Kafka, RabbitMQ, Elasticsearch and ChromaDB are **not**
backed up — caches, replayable streams and rebuildable indexes. If that ever
stops being true for one of them, it needs a job here.

Buckets and S3 key pairs are provisioned by Terraform (`object-storage.tf`, on
the existing Contabo OAuth2 provider). Credentials are **never** created with
`kubectl create secret` — they are sealed OFFLINE into SealedSecrets in the
`fuzeinfra` namespace, committed, and synced by Argo CD.

---

## 1. Loki → S3 (`fuzeinfra-loki`)

Loki's S3 backend is already wired in the chart (`loki.s3.*` in `values.yaml`,
consumed by `templates/configmaps-monitoring.yaml` and `templates/monitoring.yaml`).
On the prod overlay (`values-contabo.yaml`) `loki.s3.enabled` is **true** as of
2026-09-01, backed by the `loki-s3` SealedSecret
(`deploy/sealed-secrets/loki-s3-credentials.yaml`, keys `LOKI_S3_ACCESS_KEY_ID` /
`LOKI_S3_SECRET_ACCESS_KEY`).

This was previously described here as a "human-gated" flip. It was not a
safeguard — a flag waiting on a human to seal a secret is a defect in an
autonomous SDLC, and while it waited, the on-disk fallback filled its PVC and
wedged prod delivery for 37 hours. The reproduction/rotation path is documented
in `deploy/sealed-secrets/loki-s3-credentials.yaml.template`; note the key pair
comes from the **Contabo API**, not the panel, and every API call needs an
`x-request-id` header that is a valid UUID.

There is **no** log migration — existing on-disk chunks age out under the Loki
retention policy; new chunks land in S3.

**Bounding growth.** S3 removes the disk ceiling but not the need for a bound —
Loki OSS has **no size-based retention, only time**. Two mechanisms apply:

- `limits_config.retention_period` = **336h (14 days)**, enforced by the
  compactor (`configmaps-monitoring.yaml`).
- the **`PVCFillingUp`** alert (`helm/fuzeinfra/rules/kubernetes.yml`) at 80%
  used, which is the only thing that catches a filling volume *before* it fills.
  Loki still keeps its WAL, tsdb-shipper cache and compactor scratch on the PVC.

---

## 2. Scheduled DB backups → S3 (`fuzeinfra-backups`)

`templates/backup-cronjobs.yaml` creates one CronJob per database, gated by
`backups.enabled` (default false) and per-DB `backups.<db>.enabled`. Each pod:

- **initContainer `dump`** runs in the database's own image (so it ships the
  native dump tool), connects to the in-namespace Service, and writes one
  compressed file to a shared `emptyDir`:
  - **Postgres** — `pg_dumpall | gzip` (all databases + globals).
  - **MongoDB** — `mongodump --archive --gzip` (single-file archive).
  - **Neo4j** — **online** logical export via `apoc.export.cypher.all(..stream..)`
    streamed over Bolt and gzipped. Community Neo4j has no online
    `neo4j-admin backup`; APOC is present in the cluster image
    (`NEO4J_PLUGINS=["apoc"]`), so this needs no downtime.
- **container `upload`** runs the aws-cli image and `aws s3 cp`s the file to
  `s3://fuzeinfra-backups/<prefix>/<db>/fuzeinfra-<db>-<ts>.<ext>`.

DB credentials come from `fuzeinfra-secrets` (in-namespace, by key). The S3 key
pair comes from the SealedSecret named by `backups.s3.existingSecret`
(e.g. `fuzeinfra-backups-s3`, keys `BACKUP_S3_ACCESS_KEY_ID` /
`BACKUP_S3_SECRET_ACCESS_KEY`).

**Enable (human-gated):**

1. `terraform apply` the `object-storage.tf` bucket (out of scope for this PR).
2. Seal the S3 key pair offline into `fuzeinfra-backups-s3`; commit; Argo sync.
3. Set `backups.enabled: true` (and `backups.s3.endpoint`) in
   `values-contabo.yaml`; commit; Argo sync.

**Retention** (age-out of old dumps) is an **S3 bucket lifecycle policy** on
`fuzeinfra-backups`, defined in Terraform — *not* in these CronJobs. The Jobs
only ever write; the lifecycle rule expires objects older than N days.

**Restore** (manual, break-glass): download the object, then
`gunzip | psql` (Postgres, into a fresh cluster), `mongorestore --archive --gzip`
(Mongo), or `cypher-shell < dump.cypher` (Neo4j). Restore is intentionally not
automated.

### Tuning

```yaml
backups:
  enabled: true
  schedule: "0 2 * * *"        # default; per-DB `schedule:` overrides
  s3: { endpoint, region, bucket, prefix, existingSecret }
  postgres: { enabled: true, image: postgres:15, host, port }
  mongodb:  { enabled: true, image: mongo:7,     host, port }
  neo4j:    { enabled: true, image: neo4j:5,      host, boltPort }
```

---

## 3. Application blobs → S3 (`fuzeinfra-blobs`)

User-generated blobs (uploads, attachments, exports, generated media) belong in
object storage, **not** in a database column or a pod PVC. Apps talk to the
`fuzeinfra-blobs` bucket directly with any S3 SDK — FuzeInfra provisions the
bucket and hands the app a scoped key pair; it does not proxy blob traffic.

Onboarding an app for blob storage:

1. Provision a bucket/prefix + a scoped S3 key pair (Terraform `object-storage.tf`).
2. Seal the key pair for the app's namespace as a SealedSecret (the app repo
   does this via the `@fuze` delegation flow — FuzeInfra is not edited directly).
3. The app reads endpoint/bucket/creds from env and uses its S3 SDK:

```
S3_ENDPOINT=https://eu2.contabostorage.com
S3_REGION=default
S3_BUCKET=fuzeinfra-blobs
S3_FORCE_PATH_STYLE=true          # required for Contabo/MinIO-style endpoints
S3_ACCESS_KEY_ID / S3_SECRET_ACCESS_KEY   # from the SealedSecret
```

Contabo (like MinIO) requires **path-style** addressing and a non-AWS
`endpoint`. Most SDKs need both `forcePathStyle: true` and an explicit
`endpoint` set. Presigned URLs work for direct browser upload/download without
routing bytes through the app.

---

## 4. Prometheus TSDB → S3 (`fuzeinfra-backups`, prefix `volumes/`)

### Why this exists

On **2026-09-07** two durable nodes were reinstalled 25 minutes apart. The
Longhorn replicas of `fuzeinfra-prometheus-data` were still rebuilding from the
first reinstall when the second one was wiped; the volume went
`robustness: faulted` with **zero** recoverable replicas and every metric the
cluster had was lost. The per-database CronJobs in §2 did not cover it, because
Prometheus has no logical dump tool — its state is a file tree.

### Why not `tar /prometheus`

Copying the TSDB directory while Prometheus is running produces a **corrupt,
unrestorable** snapshot: the head block is being written, the WAL is
mid-segment, and compaction can delete a block out from under the reader.
Prometheus ships the right primitive — `POST /api/v1/admin/tsdb/snapshot`
creates a consistent, hard-linked copy under `<tsdb.path>/snapshots/<name>`.
That endpoint requires `--web.enable-admin-api`, which `templates/monitoring.yaml`
adds **only** when `backups.volumes.prometheus.enabled` is true (the same admin
API also exposes `delete_series` to anything that can reach port 9090
in-cluster, so it is not left on unconditionally).

### How the job works

`templates/backup-volume-cronjobs.yaml`, gated by `backups.enabled` **and**
`backups.volumes.prometheus.enabled`, requires `backups.sink: "s3"`:

- **initContainer `snapshot`** (`curlimages/curl`) prunes any snapshot a previous
  run abandoned — they are hard links and pin blocks retention has already
  dropped — then POSTs the snapshot API and records the returned name.
- **container `upload`** (`amazon/aws-cli`) `aws s3 sync`s the snapshot tree to
  `s3://fuzeinfra-backups/volumes/prometheus/<ts>/tsdb/`, writes a `MANIFEST`
  object **last**, and removes the local snapshot so its hard links stop pinning
  blocks.

Both mount the TSDB PVC with `subPath: snapshots`, so the blocks themselves are
not reachable from this pod. The pod carries a **required podAffinity** onto the
Prometheus pod: `ReadWriteOnce` is per-*node*, so co-location is what makes the
mount legal. If Prometheus is down the Job pod stays `Pending` and the Job fails
on `activeDeadlineSeconds` — visible as `kube_job_failed`, never a silent skip.

The credential is the **same** `fuzeinfra-backups-s3` SealedSecret the DB dumps
use. Nothing new is provisioned.

**Retention** is a second **bucket lifecycle rule** asserted by the same PostSync
hook Job as §2 (`backup-s3-lifecycle.yaml`) —
`backups.volumes.lifecycleExpireDays`, **14 days** in prod against the dumps'
30, because a run is tens of GB rather than a few MB. Both rules go in one
`put-bucket-lifecycle-configuration` call, which is a full replace.

### Restore (break-glass, manual)

**What you lose:** everything scraped between the last successful nightly run
(03:20 UTC in prod) and the failure — **up to 24 hours of metrics**, plus
anything older than the restored snapshot's own retention window. This is a
disaster-recovery floor, not continuous protection; continuous would be Thanos
(below).

1. **Pick a backup and prove it is complete.** A prefix without `MANIFEST` is a
   half-finished sync — do not restore it.
   ```bash
   aws --endpoint-url https://eu2.contabostorage.com \
     s3 ls s3://fuzeinfra-backups/volumes/prometheus/
   aws --endpoint-url https://eu2.contabostorage.com \
     s3 cp s3://fuzeinfra-backups/volumes/prometheus/<TS>/MANIFEST -
   ```
2. **Stop the writer.** Prometheus must not be running against the volume while
   it is repopulated. Via Git (prod is GitOps — do not `kubectl scale`): set
   `prometheus.enabled: false` in `values-contabo.yaml`, merge, let Argo sync.
   In a genuine break-glass, an operator may scale the Deployment to 0 knowing
   Argo `selfHeal` will revert it.
3. **Get an empty volume.** If the PVC is faulted, delete the PVC and let Argo
   recreate it from `templates/monitoring.yaml`; otherwise reuse it.
4. **Copy the data back in.** Run a throwaway pod that mounts
   `fuzeinfra-prometheus-data` at `/prometheus` (any image with the aws CLI, the
   S3 env from `fuzeinfra-backups-s3`, `runAsUser/fsGroup: 65534`):
   ```bash
   aws --endpoint-url "$S3_ENDPOINT" s3 sync \
     "s3://$S3_BUCKET/volumes/prometheus/<TS>/tsdb/" /prometheus/
   ```
   The snapshot's layout **is** a TSDB directory — block dirs plus `chunks_head`
   — so it goes in at the root of `storage.tsdb.path`, not into a subdirectory.
   A snapshot carries no WAL; Prometheus starts a fresh one.
5. **Start Prometheus** (revert step 2 through Git) and verify:
   `curl -s localhost:9090/api/v1/query?query=up | head`, and check that
   `prometheus_tsdb_head_series` is non-zero and that a range query reaches back
   into the restored window.
6. **Turn off any temporary out-of-band change** so Argo and Git agree again.

---

## Deferred: Prometheus long-term storage (Thanos)

§4 is a nightly **disaster-recovery floor**, not long-term storage: it bounds
loss at ~24h, it does not make metrics durable continuously or queryable across
restores. Long-term/HA metrics on S3 remains a separate effort via the **Thanos**
sidecar + object-store path (`thanos-store`, `thanos-compact`) writing to a
`fuzeinfra-metrics` bucket. It is **not** part of this work and is tracked
separately; the DB-backup, Loki and TSDB-backup paths above do not depend on it.
