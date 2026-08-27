# A2A delivery gateway (v0)

Wakes **and** delivers into cloud Claude Code sessions — including **idle** ones, which
the in-sandbox WSS bridge can't do (the sandbox freezes background processes when a
session is idle, so its listener is down exactly when you need it).

The only thing that wakes an idle cloud session is Anthropic server-side. This gateway
runs on infra we control, holds a **Claude.ai account login**, and shells out to the
documented primitive:

```
claude -p "<message>" --cloud <session-id>      # queues the message AND wakes the session
```

```
Session A (active) ──outbound HTTPS──► a2a-gateway.<domain> ──► claude -p --cloud <B-id>
                                          (registry: name→cse_id)   └─ Anthropic WAKES B + delivers
```

**Validated (2026-08-27):** an idle/archived cloud session was woken by a server-side
follow-up (the web-UI form of `--cloud`), processed the message, and its bridge
reconnected to the relay on wake (`/health` → `connected:true`).

## Endpoints (bearer-gated except /healthz)
- `GET /healthz`
- `POST /register {name, session_id}` — sessions self-register on start
- `GET /registry`
- `POST /send {to, text}` — `to` = a registered name or a raw `cse_`/`session_` id →
  runs `claude -p <text> --cloud <id>` → returns the CLI JSON

## Auth / credential (the crux)
- **Account, not API key.** `--cloud` rejects API keys ("API key authentication is not
  sufficient"). The gateway needs a **Claude.ai account** `.credentials.json` from
  `claude auth login --claudeai`, provided as the `a2a-gateway-secret` SealedSecret
  (`deploy/sealed-secrets/a2a-gateway-secret.yaml.template`). `ANTHROPIC_API_KEY` is
  left unset in the pod so the CLI uses the account login.
- The gateway acts **as that account**; every delivered message spends its usage. v0
  bearer = `FUZE_A2A_GATEWAY_TOKEN` (open if unset). Durable auth + per-agent identity
  → FuzeAgent / FuzeSDLC#219.

## Deploy
- Helm: `helm/fuzeinfra/templates/a2a-gateway.yaml`, gated `a2aGateway.enabled`
  (OFF until the secret is landed). ConfigMap-embedded, run by stock `node:20-slim`
  that `npm i -g @anthropic-ai/claude-code` at start (no image build for v0).
- CF Access bypass for `a2a-gateway.<domain>`: terraform (follow-up, like the relay).

## Session side
- Register on start: `POST /register {name, session_id:$CLAUDE_CODE_REMOTE_SESSION_ID}`
  (SessionStart hook). Send: the `a2a_send` MCP tool POSTs `/send`. Env: `FUZE_A2A_GATEWAY_URL`.

## Why this supersedes the WSS relay for delivery
The relay only routes between sessions **both active at once**; it can't wake an idle
peer. The gateway's `claude -p --cloud` wakes idle peers, so it's the real delivery
path. The relay stays useful only for low-latency active↔active + presence. Graduates
to **FuzeAgent**.
