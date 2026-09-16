# `docker/mariadb/init` — local mirror of `serviceMariadbDatabases`

This directory is mounted at `/docker-entrypoint-initdb.d` in the compose
`mariadb` service. MariaDB runs everything here **once**, on FIRST
initialization only (empty datadir) — exactly like `docker/postgres/init`.

It is the **local/compose** half of per-service MariaDB provisioning. The
authoritative, GitOps half is the Helm values list `serviceMariadbDatabases`
(`helm/fuzeinfra/values.yaml`), applied by the idempotent PostSync Job in
`helm/fuzeinfra/templates/service-mariadb-provisioning.yaml`.

## Adding a consumer

1. Add the allocation to `serviceMariadbDatabases` in
   `helm/fuzeinfra/values.yaml` (gated `enabled: false`) and to
   `governance/datastore-allocations.md`. That is the source of truth.
2. Mirror it here as `NN-<app>.sql`, written to be idempotent
   (`CREATE DATABASE IF NOT EXISTS` / `CREATE USER IF NOT EXISTS`), so a local
   stack matches prod.
3. **Never commit a real password here.** Local files use a throwaway
   placeholder or read `${...}` from `.env`; the real password lives only in
   the consumer's SealedSecret. See
   `docs/consuming-repos/MARIADB_PROVISIONING.md`.

## Template

```sql
CREATE DATABASE IF NOT EXISTS `myapp`
  CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER IF NOT EXISTS `myapp_svc`@`%` IDENTIFIED BY 'local_dev_only_password';
GRANT ALL PRIVILEGES ON `myapp`.* TO `myapp_svc`@`%`;
FLUSH PRIVILEGES;
```

Note the grant is scoped to `` `myapp`.* `` — one database, no global
privileges. That is the same least-privilege shape the Helm Job enforces.
