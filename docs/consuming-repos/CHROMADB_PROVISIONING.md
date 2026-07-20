# ChromaDB Consuming-Repo Provisioning

## Pattern

Add the consumer's shared/bootstrap collections to `serviceChromaCollections`
in the applicable FuzeInfra Helm values overlay. The PostSync provisioning Job
uses ChromaDB's idempotent `get_or_create_collection()` API after each install
or upgrade.

ChromaDB is unauthenticated and internal to the cluster, so no credential or
per-service user is required. Use a `<service>_<purpose>` prefix to avoid name
collisions. This is logical namespacing, not access control.

Dynamic per-resource collections such as `repo_<projectId>` must not be added
to this registry; the consuming service creates those on demand.

## Existing consumers

| Repo / service | Bootstrap collections | Dynamic collections |
|---|---|---|
| FuzePlan repo-digester | `_repo_digester_ready` | `repo_<projectId>` (service-managed) |

## Adding a new consumer

1. Choose collection names using the `<service>_<purpose>` convention.
2. Add an enabled `serviceChromaCollections` entry to the target environment's
   Helm values file, listing only shared/bootstrap collections.
3. Run `helm lint` and render the target overlay to verify the PostSync Job.
4. Open a FuzeInfra pull request. After merge, Argo CD runs the idempotent Job
   against the shared ChromaDB service.
5. Verify the Job succeeded and the bootstrap collections exist before marking
   onboarding complete. Do not make an imperative production change; GitOps is
   the source of truth.
