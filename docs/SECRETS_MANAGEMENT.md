# Secrets Management (Sealed Secrets)

FuzeInfra owns the **secrets-management methodology** for every FuzeOne consumer.
This is the single source of truth for how secrets are sealed, committed, and
decrypted across the platform.

The model is **asymmetric**:

- **Sealing needs only the PUBLIC cert** — anyone can encrypt, offline.
- **Decryption needs the PRIVATE key** — held only by the in-cluster
  [Sealed Secrets](https://github.com/bitnami-labs/sealed-secrets) controller.

So consumers **seal offline against a published public cert and commit only
encrypted blobs**. No consumer ever needs a kubeconfig, cluster credentials, or
the private key. Ciphertext in git is useless to anyone but the cluster.

> **TL;DR for a consumer repo**
> ```bash
> # zero cluster access required
> curl -fsSL https://sealed-secrets.prod.fuzefront.com/v1/cert.pem -o /tmp/cert.pem
> seal-secret.sh myproject/myproject-secrets DATABASE_URL=@./db-url.txt --out deploy/sealed/myproject-secrets.yaml
> git add deploy/sealed/myproject-secrets.yaml   # commit the ciphertext, nothing else
> ```

---

## 1. The published public cert (stable URL)

The controller's current public cert is served at a stable, always-current URL:

```
https://sealed-secrets.prod.fuzefront.com/v1/cert.pem
```

- **Single source of truth.** It is the live controller's `/v1/cert.pem`
  endpoint exposed through an Ingress, so it is **always current** — when the
  controller rotates its key (see [Rotation](#5-key-rotation)), the URL serves
  the new cert automatically. Never vendor a copy of the cert into a repo;
  always fetch it.
- **Public by design.** A public key can only *encrypt*. The endpoint is
  exempted from Cloudflare Access (it is the one `*.prod.fuzefront.com` host that
  does not require OTP) precisely because it is safe to expose to anyone — CI,
  scripts, and developers alike.
- **Override** with `--cert <url|file>` or the `FUZEINFRA_SEALED_CERT_URL`
  environment variable for air-gapped/offline sealing against a pinned cert file.

How it is wired (for operators):

| Piece | Where |
|-------|-------|
| Controller install + cert Ingress | `argocd/applications/sealed-secrets.yaml` (Bitnami chart, `kube-system`) |
| Public-cert Access bypass | `terraform/contabo/cloudflare.tf` → `sealed_secrets_cert` |
| Apply / re-sync | `terraform/contabo/provisioning.tf` |

The wildcard `*.prod.fuzefront.com` already routes to Traefik, so the Ingress
host rule is all that's needed to publish the endpoint.

---

## 2. Per-service SealedSecrets (least privilege)

**Seal one SealedSecret per service, not one shared kitchen-sink Secret.**

A SealedSecret is **strict-scoped** by default: it only decrypts under the exact
`namespace` + `name` it was sealed for. Per-service secrets give you:

- **Least privilege** — a service mounts only its own secret; a leak or a
  compromised pod can't read another service's credentials.
- **Independent rotation** — re-seal `billing-secrets` without touching
  `auth-secrets`.
- **Clear ownership** — each secret maps to one service and one chart.

Name them after the service that consumes them:

```
fuzefront/billing-secrets        # STRIPE_*, BILLING_INTERNAL_TOKEN
fuzefront/auth-secrets           # JWT_SECRET, OAUTH_CLIENT_SECRET
myproject/myproject-secrets      # DATABASE_URL, REDIS_URL, ...
```

Do **not** create a single `myproject/all-secrets` that every workload mounts.

---

## 3. Seal offline with `seal-secret.sh`

The canonical tool lives at [`scripts/seal-secret.sh`](../scripts/seal-secret.sh).
It fetches the current public cert, builds the Secret **in memory**, seals it,
and writes/merges the SealedSecret manifest in place. It **never prints
plaintext** and **never needs cluster access**.

Requirements: `bash`, `kubeseal`, `curl`, and `base64`/`openssl`. **No kubectl.**

```bash
# Seal literals into a new manifest (scope = ns/name):
scripts/seal-secret.sh fuzefront/billing-secrets \
  STRIPE_SECRET_KEY=@./stripe.key \
  BILLING_INTERNAL_TOKEN=@./billing.token \
  --out deploy/sealed/billing-secrets.yaml

# Add/rotate a single key — merged into the existing file in place:
scripts/seal-secret.sh fuzefront/billing-secrets \
  STRIPE_WEBHOOK_SECRET=@./whsec.txt \
  --out deploy/sealed/billing-secrets.yaml

# Seal an entire dotenv file:
scripts/seal-secret.sh myproject/myproject-secrets --env-file .env.prod \
  --out deploy/sealed/myproject-secrets.yaml

# Raw mode — print one encrypted blob for manual templating:
scripts/seal-secret.sh --raw myproject/myproject-secrets API_KEY=@./api.key
```

Guidance:

- **Prefer `KEY=@file` or `--env-file`** over `KEY=value` on the command line —
  argv is visible to other processes on the machine.
- The output manifest is **safe to commit**. Delete the plaintext inputs after
  sealing.
- `--cert <url|file>` / `FUZEINFRA_SEALED_CERT_URL` override the cert source for
  offline use.

The resulting SealedSecret is mounted by your Deployment exactly like a normal
Secret (`envFrom`/`secretKeyRef`) — see
[`DEPLOYING_A_SERVICE_TO_K8S.md` §5](./DEPLOYING_A_SERVICE_TO_K8S.md#5-secrets-the-sealedsecrets-workflow).

---

## 4. Decryption is cluster-only

Nothing outside the cluster can decrypt a SealedSecret. The controller in
`kube-system` holds the private key, watches for `SealedSecret` objects, and
materializes the real `Secret` at sync time. Consequences:

- **No consumer holds the private key or a kubeconfig.** FuzeFront and every
  other consumer are **GitOps-only**: they commit ciphertext; Argo CD + the
  controller do the rest.
- You **cannot** `unseal` a committed blob locally to recover plaintext — that is
  the point. Keep the original plaintext in your own secret manager (or
  regenerate it), not in git.
- The **private key never leaves the cluster** and is backed up by the operator
  out-of-band (see below).

---

## 5. Key rotation

The controller rotates its sealing key on a schedule (default ~30 days),
**adding** a new key while keeping old ones so previously-sealed secrets still
decrypt. Because consumers always fetch the cert from the published URL, rotation
is transparent: the next seal automatically uses the newest key. You do **not**
need to re-seal existing secrets when the key rotates.

You only re-seal when a secret's **value** changes. That must be a hands-off
automated primitive — never a manual runbook.

### Automated value rotation (`rotate-sealed-secret.yml`)

`.github/workflows/rotate-sealed-secret.yml` is the self-service rotation gate.
Trigger it (UI → Actions → *rotate-sealed-secret* → Run, or
`gh workflow run rotate-sealed-secret.yml -f scope=<ns>/<name> -f key=<KEY> -f manifest=<path>`)
and it:

1. generates a fresh value (`openssl rand -hex` + whitespace-scrubbed) — or takes
   an explicit `value` input;
2. seals it **offline** against the published public cert via `seal-secret.sh`
   (no cluster access, plaintext never logged) and merges it into the manifest;
3. opens a PR that **auto-merges on green** → Argo CD syncs the SealedSecret →
   the controller decrypts it → the Secret updates.

For the consumer to pick up the new value automatically, it must carry a
**reloader trigger** — a [stakater/reloader](https://github.com/stakater/Reloader)
annotation or a Helm `checksum/secret` pod annotation — so its pods restart on
the Secret change. (If neither is present, pass `reload_argocd_app=<app>` to have
the workflow hard-refresh + sync that Argo app.) The result: rotating a secret is
**fire-the-workflow → done**, with no human running `seal-secret.sh` by hand.

This is the family-standard primitive — replicate the workflow in each repo (like
`claude.yml` / `auto-merge.yml`) pointed at that repo's SealedSecret. An agent's
remediation for a bad/leaked secret should be *"triggered rotation → PR #N →
synced"*, never *"please run this script."*

`seal-secret.sh` remains available for the rare interactive case, but rotation
should go through the workflow.

**Operator responsibilities** (not consumers):

- **Back up the private key** so the controller can be restored to a new cluster
  without invalidating every committed SealedSecret:
  ```bash
  kubectl get secret -n kube-system \
    -l sealedsecrets.bitnami.com/sealed-secrets-key -o yaml > sealed-secrets-key-backup.yaml
  # store OUT-OF-BAND (not in git); restore with `kubectl apply` before reinstalling the controller
  ```
- **Disaster recovery**: restore the backed-up key Secret, then let Argo CD
  re-install the controller — all existing ciphertext decrypts again.
- If the private key is ever **compromised**, rotate it, then **re-seal every
  secret** against the new published cert and rotate the underlying credentials.

---

## 6. Distribution via the onboarding toolkit

The tool + cert are distributed to consumers so a joining repo is self-sealing on
day one — **no cluster access** required:

1. **Vendor the script.** Copy [`scripts/seal-secret.sh`](../scripts/seal-secret.sh)
   into the consumer repo (e.g. `deploy/scripts/seal-secret.sh`) or call it from a
   FuzeInfra checkout. It is standalone — only `kubeseal`/`curl`/`bash` needed.
2. **Point at the published cert.** The script already defaults to the published
   URL; consumers can set `FUZEINFRA_SEALED_CERT_URL` to pin or override it.
3. **Commit only ciphertext.** Add `deploy/sealed/*.yaml` to git; never commit
   plaintext `Secret` manifests or `.env` files (add them to `.gitignore`).

For the **fuzeone onboarding toolkit**, fetch both in one step:

```bash
# in a joining repo's bootstrap
curl -fsSL https://raw.githubusercontent.com/izzywdev/FuzeInfra/main/scripts/seal-secret.sh \
  -o deploy/scripts/seal-secret.sh && chmod +x deploy/scripts/seal-secret.sh
export FUZEINFRA_SEALED_CERT_URL=https://sealed-secrets.prod.fuzefront.com/v1/cert.pem
```

That gives every onboarded repo offline self-sealing with zero cluster access.

---

## 7. Rules

- **Never** commit a plaintext `Secret` manifest or a `.env` with real values.
- **One SealedSecret per service** — least privilege, independent rotation.
- **Always fetch** the cert from the published URL; never hardcode a vendored cert.
- **Seal offline**; decryption is **cluster-only**.
- **No consumer gets a kubeconfig** — sealing needs only the public cert.

## Related docs

- [`DEPLOYING_A_SERVICE_TO_K8S.md`](./DEPLOYING_A_SERVICE_TO_K8S.md) — how a service
  consumes the sealed secret in its Deployment.
- [`gitops.md`](./gitops.md) — how Argo CD delivers FuzeInfra (and the controller).
- [Bitnami Sealed Secrets](https://github.com/bitnami-labs/sealed-secrets) — upstream project.
