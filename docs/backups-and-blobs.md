# Stateful data on S3 (Contabo Object Storage)

FuzeInfra runs its stateful stores on **block / local-path** storage. Object
storage (S3-compatible, Contabo Object Storage) is **not** a live data volume for
any database — S3 has no POSIX/block semantics, so Postgres, MongoDB, Redis,
Neo4j, Elasticsearch, ChromaDB, Kafka and RabbitMQ all keep their data on the
node's block storage. S3 has exactly three roles on this platform:

| Use | Bucket | Mechanism | Default |
|-----|--------|-----------|---------|
| Loki log chunks + index | `fuzeinfra-loki` | Loki native S3 backend | off |
| Scheduled DB backup dumps | `fuzeinfra-backups` | backup CronJobs (this chart) | off |
| Application blob storage | `fuzeinfra-blobs` | app SDK (S3 client) | n/a |

Everything is **default-disabled** and every enable step is human-gated and
GitOps-driven. Prometheus long-term storage is a separate, deferred concern
(documented at the bottom).

Buckets and S3 key pairs are provisioned by Terraform (`object-storage.tf`, on
the existing Contabo OAuth2 provider). Credentials are **never** created with
`kubectl create secret` — they are sealed OFFLINE into SealedSecrets in the
`fuzeinfra` namespace, committed, and synced by Argo CD.

---

## 1. Loki → S3 (`fuzeinfra-loki`)

Loki's S3 backend is already wired in the chart (`loki.s3.*` in `values.yaml`,
consumed by `templates/configmaps-monitoring.yaml` and `templates/monitoring.yaml`).
On the prod overlay (`values-contabo.yaml`) the endpoint/bucket are filled in and
the switch is `loki.s3.enabled`, kept **false** until the credential SealedSecret
exists (flipping it on without the secret would crash-loop Loki).

**Enable (human-gated):**

1. Fetch the S3 key pair for `fuzeinfra-loki` from the Contabo panel.
2. Seal it offline into a SealedSecret named `loki-s3` in the `fuzeinfra`
   namespace with keys `LOKI_S3_ACCESS_KEY_ID` / `LOKI_S3_SECRET_ACCESS_KEY`.
   Commit it; let Argo sync.
3. Set `loki.s3.enabled: true` in `values-contabo.yaml`; commit; let Argo sync.

There is **no** log migration — existing on-disk chunks age out under the Loki
retention policy; new chunks land in S3.

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
   does this via the `@claude` delegation flow — FuzeInfra is not edited directly).
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

## Deferred: Prometheus long-term storage (Thanos)

Prometheus keeps only local TSDB today. Long-term/HA metrics on S3 is a separate
effort via the **Thanos** sidecar + object-store path (`thanos-store`,
`thanos-compact`) writing to a `fuzeinfra-metrics` bucket. It is **not** part of
this work and is tracked separately; the DB-backup and Loki paths above do not
depend on it.
