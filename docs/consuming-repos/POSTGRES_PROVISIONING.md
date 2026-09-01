# PostgreSQL Consuming-Repo Provisioning

FuzeInfra runs **one shared PostgreSQL engine**. Consuming repos do **not** ship
their own Postgres StatefulSet/PVC — they get a least-privilege **role +
database** on the shared server, created declaratively by an idempotent Argo
**PostSync Job**.

- Chart values: `serviceDatabases` / `serviceDbProvisioning`
  (`helm/fuzeinfra/values.yaml`, enabled per environment in
  `helm/fuzeinfra/values-contabo.yaml`).
- Template: `helm/fuzeinfra/templates/service-db-provisioning.yaml`.
- Registry: `governance/datastore-allocations.md`.

## What you connect to

| | |
|---|---|
| Host (in-cluster FQDN) | `fuzeinfra-postgres.fuzeinfra.svc.cluster.local` |
| Port | `5432` |
| Role | `<app>_svc` (or `<app>_user` — see the registry) |
| Database | `<app>` |
| Privileges | owner of `<app>`; `PUBLIC` is revoked from it |

```
DATABASE_URL=postgresql://<app>_svc:<password>@fuzeinfra-postgres.fuzeinfra.svc.cluster.local:5432/<app>
```

Use the **FQDN**. A bare `fuzeinfra-postgres` only resolves inside the
`fuzeinfra` namespace, and your pods are in your own namespace.

## What the Job does (and guarantees)

For every `enabled` entry, on every Argo sync:

1. `CREATE ROLE <role> LOGIN PASSWORD …` if absent, then **always**
   `ALTER ROLE … PASSWORD …` — so rotating your sealed password propagates.
2. `CREATE DATABASE <database> OWNER <role>` if absent; owner re-asserted.
3. `GRANT ALL PRIVILEGES ON DATABASE <database> TO <role>` and
   `REVOKE CONNECT ON DATABASE <database> FROM PUBLIC`.
4. `ALTER SCHEMA public OWNER TO <role>` + `GRANT ALL ON SCHEMA public`
   (Postgres 15 revokes `CREATE` on `public` from non-owners by default —
   without this your first migration fails with `permission denied for schema
   public`).

It is **idempotent and forward-only**: re-running it on every sync is a no-op.
It provisions the *container* (role, database, schema ownership) — it does
**not** run your migrations. Your service owns its schema.

## The two-step handshake (do not skip step 1)

The provisioning Job runs in the **`fuzeinfra`** namespace, because that is
where the Postgres superuser credential lives and it must never leave it. A pod
can only read Secrets in its **own** namespace. So the role password has to
exist as a Secret in `fuzeinfra`, and it must be the **same value** your service
already uses in its own `DATABASE_URL`.

**Step 1 — you seal the credential (consumer repo does this).**
Seal your app's DB password for **two** namespaces:

- your own namespace (your `DATABASE_URL` / app Secret), and
- **`fuzeinfra`**, as `<app>-db-credentials` with key `password`.

Commit the ciphertext of the `fuzeinfra`-scoped one to FuzeInfra's
`deploy/sealed-secrets/<app>-db-credentials.yaml`. Only ciphertext, only Secret
names and keys — never a plaintext password in git, an issue, a PR, or a log.

```bash
# ciphertext only; the cert is deploy/sealed-secrets/sealing-cert.pem
kubeseal --cert deploy/sealed-secrets/sealing-cert.pem \
  --namespace fuzeinfra --name <app>-db-credentials \
  --format yaml < /dev/stdin > deploy/sealed-secrets/<app>-db-credentials.yaml
```

**Step 2 — the allocation is enabled.** In the **same PR** that lands that
sealed Secret, add/flip the entry in `helm/fuzeinfra/values-contabo.yaml`:

```yaml
serviceDatabases:
  - name: myapp
    enabled: true
    role: myapp_svc
    database: myapp
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
> — including ones whose Secret does exist — and degrades the Argo PostSync
> wave for the whole `fuzeinfra` app. This has happened: the Job sat in
> `CreateContainerConfigError` for ~3.7 days because three services were
> flipped on before their Secrets landed.
>
> **Never flip `enabled: true` before the Secret exists.** Same PR, or later —
> never earlier.

## Naming

| Thing | Convention |
|---|---|
| Role | `<app>_svc` |
| Database | `<app>` |
| Sealed Secret (in `fuzeinfra`) | `<app>-db-credentials`, key `password` |

Role/database names are validated at render time against `^[A-Za-z0-9_]+$` —
`helm lint` rejects anything else before it can reach the cluster.

## Verifying

Provisioning is a **GitOps/operator step**: it happens when Argo syncs the
merged commit, not when you open the PR. Afterwards, verify from your own pod:

```bash
psql "$DATABASE_URL" -c 'SELECT current_user, current_database();'
```

Read-only cluster inspection is self-service via the `cluster-query` workflow
(`docs/consuming-repos/CLUSTER_QUERY.md`) — it refuses Secret reads and
mutating verbs, so use it for pod/Job status, not for credentials.

## Related

- `docs/consuming-repos/MARIADB_PROVISIONING.md` — same model, MySQL protocol.
- `docs/consuming-repos/MONGODB_PROVISIONING.md`,
  `docs/consuming-repos/CHROMADB_PROVISIONING.md`.
- `governance/datastore-provisioning.md` — the process + security invariants.
- `governance/datastore-allocations.md` — the allocation registry.
