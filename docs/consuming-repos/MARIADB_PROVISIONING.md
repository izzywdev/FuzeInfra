# MariaDB Consuming-Repo Provisioning

FuzeInfra runs **one shared MariaDB engine** (MySQL protocol) for consumers that
cannot speak Postgres — WordPress, Laravel, and anything else built on MySQL.
Consuming repos do **not** ship their own MariaDB StatefulSet/PVC. They get a
least-privilege **user + database** on the shared server, created declaratively
by an idempotent Argo **PostSync Job**.

- Engine: `mariadb.enabled` (`helm/fuzeinfra/values.yaml`; on in
  `values-local.yaml` and `values-contabo.yaml`), rendered by
  `helm/fuzeinfra/templates/databases.yaml`.
- Allocations: `serviceMariadbDatabases` / `serviceMariadbProvisioning`.
- Template: `helm/fuzeinfra/templates/service-mariadb-provisioning.yaml`.
- Compose/local mirror: `docker/mariadb/init/`.
- Registry: `governance/datastore-allocations.md`.

## Why you should stop running your own

A consumer-owned engine is a consumer-owned outage. `mendys-wp` ran its own
`mendys-wp-mariadb` StatefulSet whose PVC was on **`local-path`**, and a node
reinstall destroyed it. The shared engine's PVC is **Longhorn**
(`reclaimPolicy: Retain`), pinned to the durable node pool
(`node.longhorn.io/create-default-disk: "true"`), and is covered by the nightly
DB backup CronJobs — none of which a per-consumer StatefulSet ever got.

## What you connect to

| | |
|---|---|
| Host (in-cluster FQDN) | `fuzeinfra-mariadb.fuzeinfra.svc.cluster.local` |
| Port | `3306` |
| User | `<app>_svc`@`%` |
| Database | `<app>` (utf8mb4 / utf8mb4_unicode_ci) |
| Privileges | `ALL PRIVILEGES ON \`<app>\`.*` — that database and nothing else |

```
DATABASE_URL=mysql://<app>_svc:<password>@fuzeinfra-mariadb.fuzeinfra.svc.cluster.local:3306/<app>
```

WordPress:

```
WORDPRESS_DB_HOST=fuzeinfra-mariadb.fuzeinfra.svc.cluster.local:3306
WORDPRESS_DB_NAME=<app>
WORDPRESS_DB_USER=<app>_svc
WORDPRESS_DB_PASSWORD=<from your own sealed Secret>
```

Use the **FQDN**. A bare `fuzeinfra-mariadb` only resolves inside the
`fuzeinfra` namespace, and your pods are in your own namespace.

The grant host is `%` — any pod on the cluster network. The **credential** is
the boundary, not the source IP; a per-namespace NetworkPolicy is the right tool
if you need a network boundary too.

## What the Job does (and guarantees)

For every `enabled` entry, on every Argo sync:

1. `CREATE DATABASE IF NOT EXISTS \`<database>\` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci`
2. `CREATE USER IF NOT EXISTS \`<user>\`@\`<host>\` IDENTIFIED BY …`, then
   **always** `ALTER USER … IDENTIFIED BY …` — so rotating your sealed password
   propagates on the next sync.
3. `GRANT ALL PRIVILEGES ON \`<database>\`.* TO \`<user>\`@\`<host>\`` +
   `FLUSH PRIVILEGES`. Nothing global. (MySQL/MariaDB has no `PUBLIC` grant, so
   unlike Postgres there is no matching `REVOKE` step — a new database is
   reachable by nobody but `root` until granted.)

It is **idempotent and forward-only**: re-running it on every sync is a no-op.
It provisions the *container* (user, database, grants) — it does **not** run
your migrations. Your service owns its schema.

The MariaDB **root** credential never leaves the `fuzeinfra` namespace and is
handed to the client through a `0600` file on tmpfs — never `-p<password>`
(process table) and never `MYSQL_PWD`.

## The two-step handshake (do not skip step 1)

The provisioning Job runs in the **`fuzeinfra`** namespace, because that is
where the root credential lives. A pod can only read Secrets in its **own**
namespace. So your user's password must exist as a Secret in `fuzeinfra`, and it
must be the **same value** your app already uses.

**Step 1 — you seal the credential (consumer repo does this).**
Seal your app's DB password for **two** namespaces:

- your own namespace (your app Secret / `WORDPRESS_DB_PASSWORD`), and
- **`fuzeinfra`**, as `<app>-db-credentials` with key `password`.

Commit the ciphertext of the `fuzeinfra`-scoped one to FuzeInfra's
`deploy/sealed-secrets/<app>-db-credentials.yaml`. Only ciphertext, only Secret
names and keys — never a plaintext password in git, an issue, a PR, or a log.

```bash
kubeseal --cert deploy/sealed-secrets/sealing-cert.pem \
  --namespace fuzeinfra --name <app>-db-credentials \
  --format yaml < /dev/stdin > deploy/sealed-secrets/<app>-db-credentials.yaml
```

**Step 2 — the allocation is enabled.** In the **same PR** that lands that
sealed Secret, add/flip the entry in `helm/fuzeinfra/values-contabo.yaml`:

```yaml
serviceMariadbDatabases:
  - name: myapp                 # logical name; becomes the SVC_PW_MYAPP env var
    enabled: true
    user: myapp_svc
    database: myapp
    # host: "%"                 # optional, default "%"
    # charset: utf8mb4          # optional
    # collation: utf8mb4_unicode_ci   # optional
    passwordSecret:
      name: myapp-db-credentials
      key: password
```

Helm **replaces** lists, it does not merge them — restate the whole list in the
overlay.

> ### The hard gate — why order matters
> The hook is a **single Job pod** that mounts *every* enabled entry's password
> via `secretKeyRef` (`optional: false`). **One** missing Secret fails that pod
> with `CreateContainerConfigError`, which blocks **all** the other entries too
> and degrades the Argo PostSync wave for the whole `fuzeinfra` app. This is not
> hypothetical — it happened on the Postgres list for ~3.7 days.
>
> **Never flip `enabled: true` before the Secret exists.** Same PR, or later —
> never earlier.

The template also refuses to render if `serviceMariadbDatabases` has enabled
entries while `mariadb.enabled` is `false` — otherwise the Job would wait
forever for a server that was never deployed.

## Migrating off your own MariaDB

1. Seal your **existing** password as `<app>-db-credentials` for `fuzeinfra`
   (step 1). Reusing the current password means your app's config barely
   changes and there is no rotation to coordinate.
2. Land the allocation with `enabled: true` (step 2) and let Argo sync. The Job
   creates the user + empty database.
3. Dump from your own engine and restore into the shared one:
   `mariadb-dump --single-transaction --routines --triggers <db>` →
   `mariadb -h fuzeinfra-mariadb.fuzeinfra.svc.cluster.local <db>`.
   Do this from a Job/pod in **your** namespace, in your own repo's chart.
4. Repoint your app at the FQDN above, verify, **then** delete your own
   StatefulSet. Delete the old PVC last, and only once you are sure.

Steps 3-4 are the consuming repo's work, not FuzeInfra's.

## Naming

| Thing | Convention |
|---|---|
| User | `<app>_svc` |
| Database | `<app>` |
| Sealed Secret (in `fuzeinfra`) | `<app>-db-credentials`, key `password` |

Identifiers are validated at render time against `^[A-Za-z0-9_]+$` (host against
`^[A-Za-z0-9_.%-]+$`) and again at runtime, so `helm lint` rejects a malformed
allocation before it can reach the cluster.

## Local / compose

The compose stack ships the same engine as `fuzeinfra-mariadb` on port `3306`
(`MARIADB_ROOT_PASSWORD` from `.env`). Mirror your allocation as idempotent SQL
in `docker/mariadb/init/NN-<app>.sql` — see that directory's README. On kind,
`values-local.yaml` enables the engine.

## Verifying

Provisioning is a **GitOps/operator step**: it happens when Argo syncs the
merged commit, not when you open the PR. Afterwards, verify from your own pod:

```bash
mariadb -h fuzeinfra-mariadb.fuzeinfra.svc.cluster.local -u <app>_svc -p \
  -e 'SELECT CURRENT_USER(); SHOW DATABASES;'
```

You should see exactly `information_schema` and your own database — if you can
see anyone else's, that is a bug, report it.

## Related

- `docs/consuming-repos/POSTGRES_PROVISIONING.md` — same model, Postgres.
- `docs/consuming-repos/MONGODB_PROVISIONING.md`,
  `docs/consuming-repos/CHROMADB_PROVISIONING.md`.
- `governance/datastore-provisioning.md` — the process + security invariants.
- `governance/datastore-allocations.md` — the allocation registry.
