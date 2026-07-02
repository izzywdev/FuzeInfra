# Datastore allocation registry

Names, prefixes, and indexes only — **never credentials**. Update in the same
PR (or provisioning run) that creates an allocation. See
`governance/datastore-provisioning.md` for the process.

## Postgres (shared `fuzeinfra-postgres`)

| App | Role | Database | Consumer repo | Status |
|---|---|---|---|---|
| fuzekeys | `fuzekeys_user` | `fuzekeys` | izzywdev/FuzeKeys | active (FuzeInfra#136) |

## Redis (shared `fuzeinfra-redis`)

| App | ACL user | Key prefix | DB index | Consumer repo | Status |
|---|---|---|---|---|---|
| fuzekeys | `fuzekeys` | `fuzekeys:` | 1 | izzywdev/FuzeKeys | active (FuzeInfra#136) |

DB index 0 is reserved for FuzeInfra platform services.
