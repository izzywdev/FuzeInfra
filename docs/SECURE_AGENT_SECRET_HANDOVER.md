# Secure agent-to-agent secret handover

How one Fuze agent (a **provider**, e.g. FuzeInfra provisioning a DB credential)
hands a password or token to another agent (a **consumer**, e.g. FuzeSocial)
**without a human in the loop and without plaintext ever touching a GitHub
issue, PR, comment, chat message, or CI log.**

This is the generic answer to "provision X and deliver the credential to repo Y".
It supersedes the *punt-to-a-human* fallback: an agent must never ask a person to
copy/paste a secret between repos. It builds on primitives this platform already
runs — **Sealed Secrets** (`docs/SECRETS_MANAGEMENT.md`), the shared **k3s
cluster + namespace RBAC**, and the **allocation registry**
(`governance/datastore-allocations.md`).

---

## The trust model (why this is safe)

Every Fuze agent that needs a shared secret operates against the **same trusted
k3s cluster** and owns a **namespace**. Two facts make a confidential channel
without any shared human:

1. **Kubernetes Secrets + namespace RBAC** are a confidential channel. A value in
   `fuzesocial/…` is readable only by that namespace's workloads and by an
   operator/agent with cluster access. Plaintext placed there never leaves the
   cluster.
2. **Sealed-Secret ciphertext is safe to publish anywhere.** Sealing uses the
   controller's *public* cert; only the in-cluster *private* key can decrypt. So
   a SealedSecret manifest can be committed to git, pasted into an issue, or
   printed in a log with zero exposure — it is useless to anyone but the target
   cluster.

Everything below is a consequence of those two facts. **A one-way fingerprint**
(`sha256(value)[:16]`) is also safe to publish, and lets both sides confirm they
hold the *same* version of a secret without either ever revealing it.

---

## Choose a channel by what the provider can reach

| # | Channel | Use when | Plaintext leaves cluster? |
|---|---------|----------|---------------------------|
| A | **Consumer-authoritative align** (preferred) | Consumer can pre-mint its own secret | **No** — secret is born in the consumer namespace; provider only aligns the backing resource to it |
| B | **In-cluster dead-drop** | Provider must mint; needs immediate runtime delivery | **No** — plaintext written straight into the consumer namespace Secret |
| C | **Sealed-ciphertext handover** | Delivery must go through the consumer's git/GitOps | **No** — only ciphertext is transmitted |
| D | **Push as GH Actions secret** | Consumer consumes it from CI, and the provider holds a cross-repo PAT | **No** — `gh secret set` is encrypted at rest; needs a PAT/App with `secrets:write` on the consumer repo |

Prefer **A** (least privilege — the provider never mints or transmits anything).
Fall back to **B/C** when the consumer can't pre-mint. Use **D** only when a
cross-repo credential is actually available (see *Enablement* below) — it is the
one channel that does **not** work from a FuzeInfra-scoped App token.

### A — Consumer-authoritative align (preferred)

1. Consumer mints the secret into its **own** namespace Secret
   (`<ns>/<app>-secrets`, well-known key, e.g. via its SealedSecret/bootstrap).
2. Consumer opens an `@claude` request on the provider repo with a STATE block and
   the intended `secretRef` + `secretKey` (**never the value**).
3. Provider creates the resource and **aligns the backing credential to the
   consumer's value**, read in-cluster
   (`ALTER ROLE <role> PASSWORD '<value-read-from-consumer-secret>'`). The secret
   is never minted or transmitted by the provider.
4. Provider posts the connection contract + `credentialFingerprint`
   (`sha256(value)[:16]`) so the consumer can confirm the alignment matches.

### B — In-cluster dead-drop

Provider mints the value and writes it directly into the consumer namespace:

```bash
PW=$(openssl rand -hex 24)                 # hex-only: no shell metachars (breaks alembic/airflow-init)
kubectl -n <consumer-ns> create secret generic <app>-secrets \
  --from-literal=DB_PASSWORD="$PW" --dry-run=client -o yaml | kubectl apply -f -
# ...set the backing resource to the same value, then discard $PW from memory
```

RBAC confines read access to the consumer namespace. No git, no issue, no log.

### C — Sealed-ciphertext handover (token-free cross-repo)

When delivery must traverse the consumer's git and the provider has **no**
cross-repo write token, hand over **ciphertext**:

```bash
kubeseal --cert https://sealed-secrets.prod.fuzefront.com/v1/cert.pem \
  --namespace <consumer-ns> --name <app>-secrets --format yaml \
  < plaintext-secret.yaml > <app>-secrets.sealed.yaml   # ciphertext — safe to publish
```

The provider posts/commits `<app>-secrets.sealed.yaml` (issue body, PR, or an
in-cluster ConfigMap the consumer reads). The consumer commits it under its
`deploy/sealed/`; Argo + the in-cluster controller decrypt it. Ciphertext is
inert everywhere except the target cluster.

### D — Push as a GitHub Actions secret

Only when the provider runner holds a PAT/App with `secrets:write` on the
consumer repo (the model in `governance/datastore-provisioning.md`):

```bash
gh secret set DB_PASSWORD -R izzywdev/<consumer> -b "$PW"   # 404s without cross-repo grant
```

---

## The handshake (all channels)

1. **Request** — consumer opens an `@claude` issue on the provider repo (STATE
   block, `secretRef`/`secretKey`, **no secret value**).
2. **Provision + place** — provider does its half in-cluster (A/B/C/D above).
3. **Pointer + fingerprint** — provider publishes the non-secret **handoff
   manifest**: `host, port, database, username, secretRef, secretKey,
   credentialFingerprint, status, issue`. Two carriers, use both when possible:
   - an **in-cluster ConfigMap** `<consumer-ns>/<app>-db-handoff` (a channel the
     consumer agent reads from its **own** namespace — needs no cross-repo GitHub
     access), and
   - a **comment on the issue** (metadata + fingerprint only).
4. **Verify + report back** — consumer rolls out, verifies connectivity **and**
   `sha256(secret)[:16] == credentialFingerprint`, then comments `DONE:`.
5. **Notify originator** — on report-back, the provider notifies the originator
   (Telegram via the platform's `repository_dispatch` notify workflows, or a
   PushNotification).

## Hard rules

- **Never** put a secret value in an issue, PR, comment, chat, or CI log. A
  `sha256[:16]` fingerprint or a SealedSecret ciphertext is the only thing that
  may appear there.
- **No long-lived plaintext stash Secrets** in the provider namespace. Delete the
  stash once handed off; if `kubectl delete` is guard-blocked on the runner,
  annotate it `fuze.io/superseded=true` and flag an operator to delete.
- Generate secrets **hex-only** (`openssl rand -hex 24`) — shell metachars break
  `airflow-init`/`alembic`.
- Record every allocation (names only) in `governance/datastore-allocations.md`.

## Enablement (the one thing that needs a human, once)

Channels **A/B/C** work from a FuzeInfra-scoped runner today. Channel **D** and
the *"open a GitHub issue on the consumer repo"* leg of the handshake require the
FuzeInfra `@claude` runner to hold a **cross-repo credential** — the GitHub App
installed on (or a PAT scoped to) `izzywdev/<consumer>` with `issues:write` +
`secrets:write`. Without it, `gh` returns `404` for the consumer repo and the
provider must fall back to the **in-cluster handoff ConfigMap (step 3)** as the
cross-agent channel, plus a cross-repo hand-off note for the org's issue monitor
to relay. Granting that App/PAT once removes the last manual dependency for all
future consumers.
