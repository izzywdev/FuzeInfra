# ChromaDB Consuming-Repo Provisioning

## Pattern

Each consumer gets a unique sealed bearer token and its own Chroma tenant and
database. Chroma enforces that binding server-side. Collection prefixes remain
useful for readability, but are never treated as access control. A NetworkPolicy
also limits connections to the registered namespace and pod labels.

Dynamic per-resource collections such as `repo_<projectId>` must not be added
to this registry; the consuming service creates those on demand.

## Existing consumers

| Repo / service | Tenant / database | Bootstrap collections | Dynamic collections |
|---|---|---|---|
| FuzePlan repo-digester | `fuzeplan` / `repo-digester` | `_repo_digester_ready` | `repo_<projectId>` (service-managed) |
| FuzeQuality | `fuzequality` / `fuzequality` | `_fuzequality_ready` | service-managed |

## Adding a new consumer

1. Generate a random token without logging it. Seal the same value for the
   FuzeInfra credential Secret and the consumer Deployment's namespace; commit
   ciphertext only. Never place a token in values, a PR, an issue, or logs.
2. Add the strict-scoped FuzeInfra SealedSecret and an enabled
   `serviceChromaCollections` allocation in the same PR. Declare a unique
   tenant/database, the secret reference, exact namespace/pod selectors, and
   only shared/bootstrap collections.
3. Configure the consumer's Chroma client with its token, tenant, and database.
   It must send the token in the `Authorization: Bearer` header.
4. Run `helm lint`, render every overlay, and validate with kubeconform. The
   rendered StatefulSet must use the pinned image and auth providers; the Job
   must contain positive own-tenant and negative cross-tenant checks.
5. Merge through GitOps. Argo runs the idempotent PostSync Job. Do not mutate
   the production cluster manually. A successful Job proves bootstrap CRUD and
   cross-tenant denial; production verification happens after the publisher's
   merge/sync, outside this implementation run.
