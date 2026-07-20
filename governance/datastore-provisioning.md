# Shared datastore provisioning (Postgres / Redis / Mongo / Neo4j / ChromaDB)

How a consumer repo gets a least-privilege silo on FuzeInfra's shared datastores
— **without any credential ever appearing in plaintext in git, issues, or logs**.

> FuzeInfra is the datastore master. Consumers never hold superuser creds and
> never operate the cluster. All provisioning is delegated to FuzeInfra's
> `@claude` handler, whose runner holds the prod `KUBE_CONFIG` and a cross-repo
> `GH_TOKEN` PAT (see `.github/workflows/claude.yml`).

> **Credential delivery (agent → agent, no human):** the "push GH secret" step
> below is only one of four delivery channels. When the runner's token is *not*
> granted on the consumer repo (`gh` returns `404`), fall back to a token-free
> channel — in-cluster dead-drop / sealed-ciphertext / consumer-authoritative
> align — per **[`docs/SECURE_AGENT_SECRET_HANDOVER.md`](../docs/SECURE_AGENT_SECRET_HANDOVER.md)**.
> Never punt the delivery to a human.

## The flow (push, not pull)

1. **Consumer opens an `@claude` issue on FuzeInfra** naming only the app
   (e.g. "provision Postgres + Redis for `fuzekeys`"). The issue **must not**
   contain proposed usernames, passwords, or connection strings — issues are
   plaintext and public. Requesting "create user xyz with password pass123"
   is forbidden.
2. **FuzeInfra's `@claude` provisions on the runner.** Credentials are
   generated runner-side (`openssl rand -hex 24` — hex-only, no shell
   metachars) and exist only in runner memory. Superuser access is read
   directly from the cluster secret via `kubectl` — never echoed.
3. **FuzeInfra pushes the creds to the consumer repo** as GitHub Actions
   secrets via `gh secret set -R izzywdev/<consumer>` using `GH_TOKEN`.
   Direction matters: a workflow's `GITHUB_TOKEN` cannot write Actions
   secrets even on its own repo, so the consumer can never pull — the PAT
   holder (FuzeInfra's handler) pushes.
4. **Registry updated.** Every allocation is recorded in
   `governance/datastore-allocations.md` (names/prefixes/indexes only, never
   secrets) so future allocations don't collide.
5. **Done + hand-off.** FuzeInfra's `@claude` comments `DONE:` on the issue
   listing the secret **names** (never values), then opens a hand-off issue on
   the consumer repo mentioning its `@claude` ("creds delivered as `<APP>_DB_*`,
   run seal-secrets, verify migrations").

## Naming convention (deterministic, per app `<app>`)

| Thing | Name |
|---|---|
| Postgres role | `<app>_user` |
| Postgres database | `<app>` (owner = `<app>_user`) |
| Redis ACL user | `<app>` |
| Redis key prefix | `<app>:` (mandatory in app code) |
| Redis DB index | next free index in the registry (tidiness only — see below) |
| GH Actions secrets | `<APP>_DB_USER`, `<APP>_DB_PASSWORD`, `<APP>_REDIS_URL` |

Hosts follow the published contract: `fuzeinfra-<svc>.fuzeinfra.svc.cluster.local`.

## Postgres recipe (runner-side)

```bash
PW=$(openssl rand -hex 24)   # hex-only: FastAPI/alembic init breaks on shell metachars
kubectl exec -n fuzeinfra <postgres-pod> -- psql -U postgres \
  -c "CREATE ROLE <app>_user LOGIN PASSWORD '${PW}';" \
  -c "CREATE DATABASE <app> OWNER <app>_user;" \
  -c "REVOKE CONNECT ON DATABASE <app> FROM PUBLIC;"
gh secret set <APP>_DB_USER     -R izzywdev/<consumer> -b "<app>_user"
gh secret set <APP>_DB_PASSWORD -R izzywdev/<consumer> -b "${PW}"
```

Verify from inside the cluster before reporting DONE (e.g. `kubectl exec` into
the Postgres pod and connect as the new role to the new DB).

## Redis recipe — ACL user per app, NOT the shared password

A Redis **DB index is not a security boundary**: any client holding the
instance password can `SELECT` into every DB and `FLUSHALL`. The silo is a
**per-app ACL user** (Redis 6+) restricted to the app's key prefix:

```bash
RPW=$(openssl rand -hex 24)
kubectl exec -n fuzeinfra <redis-pod> -- redis-cli -a "$MASTER_PW" \
  ACL SETUSER <app> on ">${RPW}" "~<app>:*" +@all -@admin -@dangerous
gh secret set <APP>_REDIS_URL -R izzywdev/<consumer> \
  -b "redis://<app>:${RPW}@fuzeinfra-redis.fuzeinfra.svc.cluster.local:6379/<n>"
```

- The key-prefix restriction (`~<app>:*`) is enforced server-side; the DB
  index `<n>` is a convention on top. App code MUST prefix all keys.
- **Persistence gotcha:** imperative `ACL SETUSER` does not survive a pod
  restart unless an `aclfile` is configured and `ACL SAVE` is run, or the ACL
  is codified in the Redis chart values. The imperative command unblocks
  go-live; the chart/values change (GitOps PR) is required follow-up before
  the issue is closed.
- If ACLs are unavailable on the deployed Redis, fall back to shared password
  + dedicated DB index, and record the accepted risk in the issue.

## ChromaDB recipe — declarative bootstrap collections

ChromaDB runs without authentication and is reachable only inside the cluster.
Consumers therefore need no ChromaDB credential or per-service user. Add an
enabled entry to `serviceChromaCollections` in the applicable Helm values
overlay; the Argo CD PostSync Job idempotently calls
`get_or_create_collection()` for every declared collection.

Use `<service>_<purpose>` collection names so consumers have distinct logical
prefixes. This namespacing prevents accidental collisions but is not an
authorization boundary: an in-cluster ChromaDB client can access every
collection while authentication is disabled.

Declare only shared/bootstrap collections. Dynamic per-resource collections,
such as `repo_<projectId>`, are created by the service on demand and are not
registered in Helm. No secret generation or credential hand-off is involved.

```yaml
serviceChromaCollections:
  - name: example-indexer
    enabled: true
    collections:
      - example_indexer_ready
```

## Security invariants

- Secrets exist only in runner memory and in the destination secret stores
  (GH Actions secrets / sealed k8s secrets). Never in git, issues, PRs, or logs.
- Passwords are hex-only (`openssl rand -hex 24`).
- One role per app per datastore; no shared roles, no superuser hand-out.
- Every allocation lands in `governance/datastore-allocations.md`.

## Future direction (not yet required)

Since consumers deploy into namespaces on the same cluster, the GH-secret hop
can be eliminated: FuzeInfra writes the creds as a plain k8s `Secret` directly
into the consumer's namespace (imperative non-chart-managed Secrets are
allowed), and the consumer chart references it via `existingSecret`, composing
`DATABASE_URL` in-pod from env. The cluster becomes the sole trust boundary.
Beyond that, a CRD-based operator (CloudNativePG-style "database request" CRs)
would make this fully declarative — revisit when consumer count grows.
