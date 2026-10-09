# Service object-storage capability

FuzeInfra, not a consuming product, owns S3/MinIO allocation, credentials and
sealed delivery. Products consume one opaque runtime configuration secret named
`ARTIFACT_STORAGE_CONFIG`; they never create buckets, users, policies, signed
URLs or long-lived object-store credentials.

## Security boundary

The existing `fuzeinfra-blobs` bucket is an infrastructure bucket. It is **not**
a product runtime capability because its account-level credential could read
other workloads. A service allocation is enabled only after its selected
S3-compatible provider creates:

1. a dedicated private bucket;
2. a dedicated workload principal whose policy permits only that bucket; and
3. one JSON `config` Secret in `fuzeinfra`, sealed at rest, containing endpoint,
   region, bucket and the workload credential.

The credential hand-off publisher re-seals that single configuration value for
the consumer namespace and target Secret. It never logs, returns, or exposes
the value to a browser. Tenant isolation below the product boundary is enforced
by FuzeX/FuzeFront Security; tenants receive neither bucket URLs nor credentials.

## Generic request flow

Consumers dispatch `provision-service-object-storage` with only a reviewed
allocation id. The workflow validates the allocation and matching disabled
handoff declaration, then opens a PR enabling both. It cannot create a provider
credential and therefore refuses an allocation until the FuzeInfra operator has
created the dedicated provider principal and strictly-scoped source Secret.

This is intentionally provider-neutral: MinIO is preferred where FuzeInfra
operates its own S3 policy engine; another S3 provider is acceptable only when
it can issue an equivalently bucket-scoped workload principal.
