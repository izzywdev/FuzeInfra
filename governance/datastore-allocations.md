# Datastore allocation registry

Names, prefixes, and indexes only — **never credentials**. Update in the same
PR (or provisioning run) that creates an allocation. See
`governance/datastore-provisioning.md` for the process.

## Postgres (shared `fuzeinfra-postgres`)

| App | Role | Database | Consumer repo | Status |
|---|---|---|---|---|
| fuzekeys | `fuzekeys_user` | `fuzekeys` | izzywdev/FuzeKeys | active (FuzeInfra#136) |
| fuzesales | `fuzesales_svc` | `fuzesales` | izzywdev/FuzeSales | declared (FuzeInfra#153) |
| fuzecontact | `fuzecontact_svc` | `fuzecontact` | izzywdev/FuzeContact | declared (FuzeInfra#153) |
| fuzeservice | `fuzeservice_svc` | `fuzeservice` | izzywdev/FuzeService | declared (FuzeInfra#153) |
| fuzesocial | `fuzesocial_app` | `fuzesocial` | izzywdev/FuzeSocial | active (FuzeInfra#150) — owner+grants verified; consumer-authoritative credential in `fuzesocial/fuzesocial-secrets:DB_PASSWORD` |

> `fuzesales` / `fuzecontact` / `fuzeservice` are provisioned **declaratively**
> by the `fuzeinfra-service-db-provision` hook Job (chart values
> `serviceDatabases` in `helm/fuzeinfra/values.yaml`, enabled in
> `values-contabo.yaml`) — the GitOps successor to the imperative recipe in
> `datastore-provisioning.md`. Each stays `enabled: false` until its consumer
> repo seals a `<app>-db-credentials` Secret (key `password`) FOR the
> `fuzeinfra` namespace, matching the password behind its own `DATABASE_URL`.

## Redis (shared `fuzeinfra-redis`)

| App | ACL user | Key prefix | DB index | Consumer repo | Status |
|---|---|---|---|---|---|
| fuzekeys | `fuzekeys` | `fuzekeys:` | 1 | izzywdev/FuzeKeys | active (FuzeInfra#136) |

DB index 0 is reserved for FuzeInfra platform services.

## ChromaDB (shared `fuzeinfra-chromadb`)

| App | Tenant | Database | Bootstrap collection | Consumer repo | Status |
|---|---|---|---|---|---|
| FuzePlan repo-digester | `fuzeplan` | `repo-digester` | `repo_digester_ready` | izzywdev/FuzePlan | declared (FuzeInfra#168 corrective follow-up) |
| FuzeQuality | `fuzequality` | `fuzequality` | `fuzequality_ready` | izzywdev/FuzeQuality | declared (FuzeInfra#168 corrective follow-up) |

Each row has a unique sealed token and an explicit NetworkPolicy peer. Tenant
and database binding is enforced by Chroma; collection prefixes are not an
authorization mechanism.
