# Longhorn S3 backup wiring (`longhorn-system`)

Off-cluster, point-in-time backups of **every Longhorn volume** to the Contabo
`fuzeinfra-backups` bucket. This closes the gap that Longhorn **replication**
leaves open: 3-replica replication protects against *node* loss, but not against
volume corruption or an all-replicas-lost event (the 2026-09-07 incident, when
`fuzeinfra-prometheus-data` went `faulted` with zero recoverable replicas).

This is **not** the same as the logical DB dumps in `helm/fuzeinfra`
(`values-contabo.yaml` → `backups`). Those dump Postgres/Mongo/Neo4j/MariaDB
*application-consistently* via their native tools. This covers the **volume**
layer — including the stateful volumes that have no logical-dump path (Redis,
Kafka, RabbitMQ, Elasticsearch, ChromaDB) — and enables volume-granular restore
(restore-to-new-volume, DR volumes, cross-cluster migration). Keep both.

## Why these objects are owned by a separate Argo app

`longhorn-system` is **not** an allowed destination of the `fuzeinfra`
AppProject (`argocd/projects/fuzeinfra.yaml`), so neither `fuzeinfra-prod` (the
umbrella chart) nor `fuzeinfra-sealed-secrets` can own anything here — routing a
`longhorn-system` object through either would fail the whole app with
`InvalidSpecError` and freeze prod. They are synced instead by
`argocd/applications/longhorn-backup.yaml`, which runs under `project: default`
(like the Longhorn controller app itself).

For the same reason the credential SealedSecret lives **here**, not under
`deploy/sealed-secrets/` (which the `fuzeinfra`-project sealed-secrets app globs
into the `fuzeinfra` namespace).

## "Why AWS_*? we use Contabo"

S3 is a protocol, not a provider. Longhorn — like the `aws` CLI and every S3 SDK
— hardcodes the credential field names `AWS_ACCESS_KEY_ID` /
`AWS_SECRET_ACCESS_KEY` / `AWS_ENDPOINTS`. `AWS_ENDPOINTS` pointed at
`eu2.contabostorage.com` is exactly what sends the traffic to **Contabo** and
never to Amazon (no Amazon account, no AWS bill). Same Contabo bucket and the
same account key pair the DB dumps already use — only the key *names* differ
because Longhorn insists on these. See the `.template` for the sealing steps.

## Files

| File | What |
|------|------|
| `longhorn-s3-credentials.yaml.template` | Sealing instructions (no secret). The real `longhorn-s3-credentials.yaml` is produced offline and committed next to it. |
| `recurringjob-daily-backup.yaml` | Nightly `backup` RecurringJob on the `default` group → every volume. |

The backup **target** itself is set in `argocd/applications/longhorn.yaml`
(`defaultBackupStore`), not here, because that is a Longhorn-chart value.

## Enable sequence (human-gated, GitOps)

1. Longhorn controller installed + healthy (sync `argocd/applications/longhorn.yaml`).
2. Seal the credential. Either **hands-off** — run the
   `seal-longhorn-s3.yml` workflow (`gh workflow run seal-longhorn-s3.yml`),
   which fetches the Contabo Object Storage key pair via the API using the
   repo's existing Contabo API secrets, seals it offline, and opens an
   auto-merging PR (no human handles plaintext) — or **manually** offline per
   `longhorn-s3-credentials.yaml.template`. Either way, commit the encrypted
   `longhorn-s3-credentials.yaml`.
3. Merge the `defaultBackupStore` change in `argocd/applications/longhorn.yaml`
   and sync the Longhorn app.
4. `kubectl apply -f argocd/applications/longhorn-backup.yaml` and sync it.

## Verify

```bash
# Target reachable (AVAILABLE must be true):
kubectl -n longhorn-system get backuptarget default
# RecurringJob present:
kubectl -n longhorn-system get recurringjob daily-backup
# Force one run instead of waiting for 04:40 UTC, then watch backups appear:
kubectl -n longhorn-system get backups
```

Also confirm from the public read path (job log) with `cluster-query.yml`:
`-n longhorn-system get backuptarget default -o wide`.

## Rollback

Delete the `longhorn-backup` Application (`kubectl delete -f
argocd/applications/longhorn-backup.yaml`) and revert `defaultBackupStore` in
`argocd/applications/longhorn.yaml`. Existing backups in the bucket are retained;
volumes keep serving from their replicas regardless.
