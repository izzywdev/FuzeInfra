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
| **Protocol** | OpenAI-compatible. `POST /chat/completions` (streaming + non-streaming) and `POST /embeddings`. The `/v1`-prefixed forms work too. Anthropic-format clients use `POST /v1/messages` — see [Anthropic-format clients](#anthropic-format-clients-claude-code) below. |

Plain HTTP, no TLS — this is cluster-internal traffic on the pod network. A
NetworkPolicy restricts it to the namespaces listed in
`networkPolicy.allowedNamespaces` (`helm/litellm/values-contabo.yaml`) — if your
service gets a connection timeout rather than a 401, your namespace is not on
that list yet. Add it in a PR.

> **Correction (2026-07-29).** This section previously said there was "no Ingress,
> no Cloudflare tunnel route, and no CF Access app". That has not been true since
> the admin UI was enabled: `values-contabo.yaml` sets `ingress.enabled: true`, so
> the gateway is reachable at `https://litellm.prod.fuzefront.com` under the
> `*.prod` wildcard — and because LiteLLM serves the UI and the API from one port,
> `/chat/completions` and `/v1/messages` are exposed at that hostname too. The
> gates are Cloudflare Access email-OTP plus `LITELLM_MASTER_KEY` on every API
> call. In-cluster callers should still use the Service DNS above: it is the path
> the NetworkPolicy is written for, and it avoids a tunnel round-trip.

## Models

Ask for these **by name**; the gateway maps each to a real upstream model.

| Model name | Routes to | Use for |
|---|---|---|
| `claude-opus-4-5` | `anthropic/claude-opus-4-5-20251101` | Chat. Legacy alias — kept for compatibility, migrate off it. |
| `claude-opus-5` | `anthropic/claude-opus-5` | Chat. **Current default choice.** |
| `claude-sonnet-5` | `anthropic/claude-sonnet-5` | Chat, cheaper/faster. |
| `claude-haiku-4-5` | `anthropic/claude-haiku-4-5` | Cheap, latency-sensitive. |
| `gpt-4.1` / `gpt-4.1-mini` | `openai/…` | Chat. Directly requestable, but their real job is being fallback targets. |
| `gemini-2.5-pro` / `-flash` / `-flash-lite`, `gemini-2.0-flash` / `-lite` | `gemini/…` | Chat. Last fallback hop. |
| `text-embedding-3-small` | `openai/text-embedding-3-small` | Embeddings. |

Adding a model is a PR against `models:` in `helm/litellm/values.yaml`. That
indirection is the point of running a gateway — do not work around it by calling
a provider directly.

### Cross-provider fallback — you do not implement this

Ask for a Claude model and you get one, unless that provider is failing, in which
case the router silently retries the **same request** against the next model and
returns a normal 200. You never see the upstream failure and you write no failover
code — a client that implements its own would have to hold every provider's key,
which is exactly what the gateway exists to prevent.

The chain is tier-matched, so an outage never quietly upgrades or downgrades what
you get (`routerSettings.fallbacks`):

| Primary | then | then |
|---|---|---|
| `claude-opus-5`, `claude-opus-4-5` | `gpt-4.1` | `gemini-2.5-pro` |
| `claude-sonnet-5` | `gpt-4.1` | `gemini-2.5-flash` |
| `claude-haiku-4-5` | `gpt-4.1-mini` | `gemini-2.5-flash-lite` |

Hops stop at the first success, so a dead last hop costs nothing — Gemini is
currently quota-exhausted and that is fine. This exists because on 2026-07-29 the
Anthropic account's credit ran out and took every chat turn down while the gateway
itself was perfectly healthy.

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

## Anthropic-format clients (Claude Code)

Claude Code speaks the **Anthropic Messages** format, not OpenAI's, so it does not
use `/chat/completions`. Point it at the gateway and it calls `POST /v1/messages`;
LiteLLM bridges that to whichever provider the router picks.

`.github/workflows/a2a-maintain.yml` is the worked example — a CI agent that holds
**no provider key at all**:

```yaml
env:
  ANTHROPIC_BASE_URL: http://litellm.fuzeinfra.svc.cluster.local:4000
  ANTHROPIC_MODEL: claude-opus-5           # must be a name in `models:` below
  ANTHROPIC_DEFAULT_HAIKU_MODEL: claude-haiku-4-5
  CLAUDE_CODE_DISABLE_1M_CONTEXT: "1"
  CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS: "1"

steps:
  - uses: anthropics/claude-code-action@<sha>
    env:
      ANTHROPIC_AUTH_TOKEN: ${{ secrets.LITELLM_MASTER_KEY }}
    with:
      anthropic_api_key: ${{ secrets.LITELLM_MASTER_KEY }}
```

Four things that are easy to get wrong:

- **Pass the key twice.** LiteLLM reads `Authorization: Bearer`, which only
  `ANTHROPIC_AUTH_TOKEN` sets — but the action refuses to launch without its
  `anthropic_api_key` input, and that copy (sent as `x-api-key`) is ignored. Omit
  either and it fails, in two different confusing ways.
- **Pin every model alias.** Unpinned, Claude Code uses its own built-in IDs and the
  gateway 400s on a name it has never heard of. The original outage requested
  `claude-opus-5[1m]` — the `[1m]` is the extended-context marker, not a model here.
- **The job must run in-cluster** (`runs-on: staging`). A hosted runner cannot reach
  a ClusterIP Service; `arc-runners` is on the NetworkPolicy allowlist for this.
- **Set `CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS=1`.** Pre-release fields
  (`context_management`, beta tool schema fields) are rejected by a non-Anthropic
  upstream, so leaving them on breaks the fallback hop specifically.

Anthropic [does not support routing Claude Code to non-Claude models through a
gateway](https://code.claude.com/docs/en/llm-gateway). The fallback hops here are
configured to work anyway — see `additional_drop_params` in `values.yaml` — but
treat a fallback-served agent run as best-effort, not a supported configuration.

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

## Related

- [`CHROMADB_PROVISIONING.md`](CHROMADB_PROVISIONING.md) — vector store for RAG.
  Note the Chroma Service is named **`fuzeinfra-chromadb`**, so the correct URL
  is `http://fuzeinfra-chromadb.fuzeinfra.svc.cluster.local:8000`.
