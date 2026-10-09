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

The sealed JSON uses this minimal contract (the provider values are never
committed): `provider: "s3"`, `endpoint` (optional HTTPS URL), `region`,
`bucket`, `accessKeyId`, `secretAccessKey`, and optional `sessionToken` or
`forcePathStyle`. The principal grants only the FuzeX bucket's object read/write
operations; it must not grant account administration, unrelated buckets, bucket
listing, or browser presigning. The allocator verifies that the source is a
strictly scoped ciphertext-only `SealedSecret` before it can enable delivery.

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

## Rollout and rollback

1. Create the bucket and least-privilege workload principal in the chosen
   provider, then seal the opaque JSON as the allocation's declared source
   manifest. Do not put any plaintext or provider account credential in Git.
2. Dispatch the named allocation request, merge the generated FuzeInfra PR,
   then let `publish-sealed-handoff` create the consumer ciphertext PR.
3. Merge the consumer PR and enable that consumer's artifact-storage Helm value
   in the same reviewed rollout. Smoke-test an upload, seal, and authorized
   preview; no browser request should target an S3 endpoint.
4. On failure, disable the consumer Helm value first and revert the allocation
   enablement. Preserve existing objects and the sealed source until an
   authorized recovery decision; do not delete a bucket as a rollback shortcut.
