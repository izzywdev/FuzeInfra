# LiteLLM Gateway — consumer guide

The shared, OpenAI-compatible LLM gateway for the FuzeOne fleet. Your service
talks OpenAI protocol to one in-cluster endpoint and **never holds provider
credentials**. Model routing, key custody, and cost caps live on the gateway.

Chart: `helm/litellm` · Argo app: `argocd/applications/litellm.yaml` · namespace `fuzeinfra`

---

## The contract

| | |
|---|---|
| **Service DNS** | `litellm.fuzeinfra.svc.cluster.local` |
| **Port** | `4000` |
| **Base URL** | `http://litellm.fuzeinfra.svc.cluster.local:4000` |
| **Auth** | `Authorization: Bearer $LITELLM_MASTER_KEY` |
| **Protocol** | OpenAI-compatible. `POST /chat/completions` (streaming + non-streaming) and `POST /embeddings`. The `/v1`-prefixed forms work too. |

Plain HTTP, no TLS — this is cluster-internal traffic on the pod network. There
is **no Ingress, no Cloudflare tunnel route, and no CF Access app**: the gateway
holds live provider keys and is unreachable from outside the cluster by
construction. A NetworkPolicy further restricts it to namespaces listed in
`networkPolicy.allowedNamespaces` (`helm/litellm/values-contabo.yaml`) — if your
service gets a connection timeout rather than a 401, your namespace is not on
that list yet. Add it in a PR.

## Models

Ask for these **by name**; the gateway maps each to a real upstream model.

| Model name | Routes to | Use for |
|---|---|---|
| `claude-opus-4-5` | `anthropic/claude-opus-4-5-20251101` | Chat. Legacy alias — kept for compatibility, migrate off it. |
| `claude-opus-5` | `anthropic/claude-opus-5` | Chat. **Current default choice.** |
| `claude-sonnet-5` | `anthropic/claude-sonnet-5` | Chat, cheaper/faster. |
| `claude-haiku-4-5` | `anthropic/claude-haiku-4-5` | Cheap, latency-sensitive. |
| `text-embedding-3-small` | `openai/text-embedding-3-small` | Embeddings. |

Adding a model is a PR against `models:` in `helm/litellm/values.yaml`. That
indirection is the point of running a gateway — do not work around it by calling
a provider directly.

> **`/embeddings` needs an OpenAI key on the gateway.** Anthropic ships no
> embeddings endpoint, so embeddings cannot be served by an Anthropic key alone.
> If `OPENAI_API_KEY` is absent from the gateway's secret, chat works and
> `/embeddings` returns an auth error.

## Getting `LITELLM_MASTER_KEY`

SealedSecrets are **strictly scoped** — a secret sealed for `fuzeinfra` cannot be
decrypted in your namespace. So the same master-key value is sealed a second
time for yours. Open a FuzeInfra issue mentioning `@claude` with your namespace
and target Secret name; the operator runs:

```bash
scripts/seal-secret.sh <your-ns>/<your-secret> LITELLM_MASTER_KEY=@/tmp/litellm_master
```

and hands you the encrypted output to commit in **your** repo. The plaintext
never leaves their machine and is never pasted into an issue.

Rotation re-seals **both** files and merges both PRs together. Rotating only the
gateway side 401s every consumer.

## Calling it

Any OpenAI SDK works — point `baseURL` at the gateway.

```ts
const res = await fetch(`${process.env.LITELLM_URL}/chat/completions`, {
  method: "POST",
  headers: {
    "content-type": "application/json",
    ...(process.env.LITELLM_MASTER_KEY && {
      authorization: `Bearer ${process.env.LITELLM_MASTER_KEY}`,
    }),
  },
  body: JSON.stringify({
    model: process.env.LITELLM_DEFAULT_MODEL ?? "claude-opus-5",
    messages: [{ role: "user", content: "hello" }],
    stream: true,
  }),
});
```

Smoke-test from inside the cluster:

```bash
kubectl -n fuzeinfra port-forward svc/litellm 4000:4000
```

```bash
curl -s localhost:4000/v1/models -H "Authorization: Bearer $LITELLM_MASTER_KEY" | jq '.data[].id'
```

## Gotchas

- **Sampling params on Claude.** Claude Opus 5 / 4.8 / 4.7 **reject**
  `temperature`, `top_p`, and `top_k` — which OpenAI-shaped clients send by
  default. The gateway sets `drop_params: true` so they are silently dropped
  instead of 400ing. Don't turn that off.
- **Don't poll `/health`.** It requires the master key *and* round-trips every
  configured model to the provider, so it bills real tokens per call. The
  unauthenticated `/health/liveliness` and `/health/readiness` are what the
  kubelet probes use.
- **Provider keys belong on the gateway, not in your pod.** If your chart still
  plumbs `ANTHROPIC_API_KEY` into an app container, that is dead config — remove
  it. Your service should only ever hold `LITELLM_MASTER_KEY`.
- **Prod is GitOps.** Never `kubectl patch`/`edit` the live Deployment — Argo
  `selfHeal` reverts it within seconds. Change `helm/litellm`, merge to `main`.

## Operating

Enabling the gateway is gated on its secret existing, exactly like the other
credentialed services in this repo:

1. Seal `deploy/sealed-secrets/litellm-secret.yaml` — the full command is in
   `deploy/sealed-secrets/litellm-secret.yaml.template`.
2. In the **same commit**, flip `enabled: true` in
   `helm/litellm/values-contabo.yaml`.

Flipping first CrashLoops the pod with `CreateContainerConfigError`
(`LITELLM_MASTER_KEY` is a required `secretKeyRef`).

Register the Argo Application once on a new cluster:

```bash
kubectl apply -f argocd/applications/litellm.yaml
```

## The admin UI

`https://litellm.prod.fuzefront.com/ui`, reached from the Cloudflare App
Launcher tile and gated by the `*.prod.fuzefront.com` email-OTP Access app. Log
in as **`admin`** with the `LITELLM_MASTER_KEY` value — LiteLLM falls back to
the master key when `UI_USERNAME`/`UI_PASSWORD` are unset, which is why neither
is wired into the chart.

**The UI needs Postgres; the gateway does not.** Proxying (`/chat/completions`,
`/embeddings`) is stateless and ran for weeks with no database. The console is
not: the first thing it does after loading is mint a UI session key, and that is
a database write. With no `DATABASE_URL` the request never completes, the page
sits on "Loading", and Cloudflare eventually returns **error 522** for the
origin. The origin log is the tell — every
`/litellm-asset-prefix/_next/static/*` chunk returns 200 and then the log simply
stops, because uvicorn writes its access-log line when a response is *sent*, so
a request that hangs forever leaves no trace.

`database.enabled: true` (`helm/litellm/values-contabo.yaml`) wires
`DATABASE_URL` to a dedicated `litellm` database on the shared
`fuzeinfra-postgres`, created idempotently by the `ensure-database`
initContainer. Credentials come from `fuzeinfra-secrets`, the same way Airflow
connects — no SealedSecret needed, because LiteLLM lives in the `fuzeinfra`
namespace alongside them.

Two consequences worth knowing before you touch it:

- LiteLLM runs `prisma migrate deploy` at startup once `DATABASE_URL` is set, so
  cold starts are slower. The `startupProbe` allows 10 minutes.
- Reverting is one value: `database.enabled: false` restores a working gateway
  with a non-functional UI.

## Related

- [`CHROMADB_PROVISIONING.md`](CHROMADB_PROVISIONING.md) — vector store for RAG.
  Note the Chroma Service is named **`fuzeinfra-chromadb`**, so the correct URL
  is `http://fuzeinfra-chromadb.fuzeinfra.svc.cluster.local:8000`.
