# FuzeX hosted-workspace provisioning

FuzeX uses the shared PostgreSQL engine. It needs no MongoDB allocation and no
dedicated database StatefulSet. Its migrations create the `design_frames`
schema inside database `fuzex_design_frames`, owned by login `fuzex_svc`.

The allocation and `fuzex-postgres` credential handoff start disabled. FuzeX
owns the runtime declaration and dispatches the generic FuzeInfra capability
from its `request-postgres-provision.yml` workflow after this change lands on
both default branches:

```sh
gh workflow run request-postgres-provision.yml --repo izzywdev/FuzeX --ref master
```

There are no workflow inputs. FuzeX sends its bounded declaration through the
family dispatch token; FuzeInfra's generic workflow validates it against the
already-declared allocation and credential-handoff data, generates an
alphanumeric random password, seals it strictly for
`fuzeinfra/fuzex-db-credentials:password`, and opens one PR containing
ciphertext, allocation enablement and handoff enablement. A repeated dispatch
reuses the pending provisioning branch or exits if provider ciphertext is
already committed; it never silently rotates.

After merging the provisioning PR, confirm that Argo has synced its actual
commit and that `fuzeinfra-service-db-provision` succeeded using `cluster-query`.
Then dispatch the existing ciphertext delivery workflow:

```sh
gh workflow run publish-sealed-handoff.yml --repo izzywdev/FuzeInfra --ref main -f id=fuzex-postgres
```

This opens a FuzeX `master` PR with strict ciphertext for
`fuzex/fuzex-design-frames-db:DATABASE_URL` under
`deploy/helm/fuzex/files/secrets/design-frames-db-sealed.yaml`. FuzeX renders that
file through its chart wrapper before its migration Sync hook. Merge it and
verify actual database authentication before enabling the lifecycle tier:

```sh
gh workflow run verify-consumer-credentials.yml --repo izzywdev/FuzeInfra --ref main -f id=fuzex-postgres
```

The scheduled publisher/verifier also covers this allocation, so later password
rotations have the same automated delivery and drift-detection path. No caller
needs cluster credentials, a plaintext URL, a local admin password, or a human
command relay. Never use `cluster-query` to read Secrets.

The shared production PostgreSQL backup CronJob already executes `pg_dumpall`
nightly at 02:00 UTC into off-cluster S3, with 30-day retention configured in
`values-contabo.yaml`; the new database is included automatically. FuzeX also
owns immutable snapshot files on its retained PVC. Database coverage alone is
not evidence of a paired content/database restore: verify the content backup
and isolated restore before declaring the migration complete.
