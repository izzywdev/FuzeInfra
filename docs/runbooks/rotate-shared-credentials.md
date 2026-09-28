# Rotating the shared datastore credentials

## What is wrong

`argocd/applications/fuzeinfra-prod.yaml` renders the chart with
`values-contabo.yaml` and nothing else. That file sets neither a `credentials:`
block nor `credentials.existingSecret`, so `templates/secrets.yaml` renders the
`fuzeinfra-secrets` Secret in **production** from the defaults committed in
`values.yaml` — whose own comment says *"Defaults mirror the docker-compose dev
values"* and *"Never commit a real value"*. The repository is public.

Argo runs `selfHeal: true` with no `ignoreDifferences` for Secrets, so what Git
renders is what is live.

## What is actually still exposed

Measured from the rendered prod chart, not assumed. `tests/test_shared_credentials_secret.py`
pins this set so it cannot grow silently:

| Key | Used by | In-datastore change needed to rotate |
|---|---|---|
| `POSTGRES_USER`, `POSTGRES_PASSWORD` | Postgres + every consumer DSN | `ALTER ROLE … PASSWORD` |
| `MONGODB_USER`, `MONGODB_PASSWORD` | MongoDB, mongo-express | `db.changeUserPassword()` |
| `NEO4J_AUTH` | Neo4j | `ALTER CURRENT USER SET PASSWORD` |
| `MARIADB_ROOT_PASSWORD` | MariaDB | `ALTER USER 'root'@'%' IDENTIFIED BY …` |
| `AIRFLOW_FERNET_KEY` | Airflow | re-encrypt or re-enter every stored Connection |
| `AIRFLOW_ADMIN_USER`, `RABBITMQ_USER` | usernames only | none |

**Already rotated out — do not re-add these to the shared Secret:**

- `GRAFANA_ADMIN_PASSWORD` → its own `grafana-admin` Secret, via `grafana.adminPasswordSecret`
- `RABBITMQ_PASSWORD` → `fuzeinfra-app-credentials`
- every consumer's database password → `<app>-db-credentials`

That per-credential pattern is the established one here, and the reasoning is
recorded inline at `helm/fuzeinfra/templates/monitoring.yaml`: a dedicated Secret
keeps each fix *contained and reversible*, whereas resealing `fuzeinfra-secrets`
wholesale means changing the Postgres, Mongo and Neo4j passwords that live
datastores are already using — a coordinated rotation, not a config change.

## Why this is not a values edit

Changing the Secret does **not** change what the datastores accept. Postgres,
MongoDB, Neo4j and MariaDB each hold their own copy of the password in their data
directory, set at first initialisation. Edit the Secret alone and the pods restart
with credentials the databases reject: every consumer app loses its datastore at
once.

`fuzeinfra-postgres` additionally consumes the Secret with `envFrom: secretRef`,
so it receives *every* key as an environment variable. For that workload the
cutover is all-or-nothing rather than per-key.

## Procedure, per credential

Do these one at a time. Each is independently revertible; a big-bang rotation is
not.

1. **Add the override knob** if the credential lacks one, mirroring
   `grafana.adminPasswordSecret` in `values.yaml` — `{name, key}`, defaulting to
   the shared Secret so the change is a no-op until pointed elsewhere.

2. **Generate and seal the new value offline.** No cluster access is required;
   sealing uses the published public key, and only the in-cluster controller can
   decrypt.

   ```sh
   # never pass the value on argv — it is visible to other processes
   printf '%s' "$(openssl rand -base64 32)" > /tmp/newpw
   scripts/seal-secret.sh fuzeinfra/postgres-credentials POSTGRES_PASSWORD=@/tmp/newpw \
       --out deploy/sealed-secrets/postgres-credentials.yaml
   shred -u /tmp/newpw
   ```

   Commit only `deploy/sealed-secrets/postgres-credentials.yaml`. The plaintext
   never enters Git.

3. **Change it inside the datastore first**, while the old value is still in the
   Secret, so nothing is broken yet. Use the table above for the statement.

4. **Point the chart at the new Secret** (`postgres.passwordSecret.name/key`),
   merge, let Argo sync.

5. **Verify** the dependent pods are Ready and one consumer app can still reach
   the datastore, before starting the next credential.

6. **Rollback** is reverting step 4; the old value is still valid in the datastore
   until step 3 is repeated in reverse.

## What a session can and cannot do

A Claude session can do steps 1, 2 and 4 — they are ordinary Git changes, and
sealing is offline against `deploy/sealed-secrets/sealing-cert.pem`.

It cannot do steps 3 and 5. Both require executing statements against live prod
datastores, and prod is read-only from every session by design (`CLAUDE.md`,
GitOps + self-heal). A session that offers to "just run the ALTER" is wrong.

## Not covered by rotation

Rotating these does not un-publish them. The current values are in the public Git
history and must be treated as compromised from the moment they were committed —
which is the argument for doing this promptly rather than tidily.
