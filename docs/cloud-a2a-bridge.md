# Cloud↔cloud A2A messaging for Claude Code sessions

**Status:** v0 (2026-08-27). Delivery gateway: `agent-templates/orchestration/a2a_gateway/`
(the path that works for idle peers). WSS relay: `agent-templates/orchestration/a2a_relay/`
(active↔active only). Session bridge: `agent-templates/environments/desktop/a2a-bridge/`.

## Delivery mechanism (validated) — the gateway, not the relay

The 2-session relay test proved the **fatal limit**: a cloud sandbox **freezes background
processes when the session is idle**, so the in-sandbox WSS bridge disconnects and can't
receive/wake an idle peer. Only Anthropic server-side can wake an idle cloud session — via
`claude -p "<msg>" --cloud <session-id>`.

**Validated 2026-08-27:** an idle/archived cloud session was woken by a server-side
follow-up (the web-UI form of `--cloud`), processed the message (`WOKE_OK` + ran commands),
and its bridge **reconnected to the relay on wake** (`/health` → `connected:true`).

So delivery goes through the **gateway** (`a2a_gateway/`): it runs on our infra, holds a
**Claude.ai account login** (`--cloud` rejects API keys), and shells out to `claude -p
--cloud`. Sessions POST `/send` to it (outbound HTTPS, works from an active session); the
gateway wakes + delivers to the target even if idle. The WSS relay remains only for
low-latency **active↔active** + presence. Both graduate to **FuzeAgent**. See
`agent-templates/orchestration/a2a_gateway/README.md`.

## Goal

Let one Claude Code **cloud** session message **another cloud session** and get a reply —
**cloud→cloud, 2-way**. Native cross-session messaging doesn't cover this (cross-machine/web
is reply-only and can't open a fresh conversation to a web session; cloud sessions are only
addressable while a *local* session is Remote-Control-connected).

## Why a relay, not a tunnel (spike finding)

The first design tried a per-session **cloudflared quick tunnel**. A live spike proved it
**cannot work in the Anthropic sandbox**: cloudflared gets a `*.trycloudflare.com` URL but
never connects — outbound port **7844** (QUIC and its TCP/HTTP2 fallback) is blocked. The
sandbox permits only **HTTPS/443 through its security proxy to allowlisted hosts**. So no
inbound tunnel can exist.

What the spike *did* confirm: the session **inbox socket** exists in cloud sessions
(`CLAUDE_CODE_MESSAGING_SOCKET`, e.g. `/tmp/cc-socks/…`) and a sibling process can open it.

So the transport is inverted: each session opens an **outbound WebSocket over 443** to a
relay we own, at `wss://relay.prod.fuzefront.com/ws` (host under the allowlisted
`*.fuzefront.com`). The relay routes by session-id; inbound messages are injected via the
local inbox socket.

```
Session A ──outbound WSS/443──► relay.prod.fuzefront.com ◄──outbound WSS/443── Session B
   register session=A                 route by 'to'                  register session=B
   inbound frame ──► write $CLAUDE_CODE_MESSAGING_SOCKET ──► new turn in A
   outbound: MCP a2a_send ──► 127.0.0.1:8760 ──► up the WS ──► relay ──► peer
```

MCP is pull-only (can't inject a turn), so **inbound = the socket**, **outbound = an MCP tool**
(`a2a_send`).

## Components

- **Relay** (`a2a_relay/relay.py`): `websockets` broker; `/ws` registers `session=<id>`, routes
  `{to,from,text,reply_to}` by id; `/healthz`. Deployed via `helm/fuzeinfra/templates/a2a-relay.yaml`
  (ConfigMap-embedded, run by stock `python:3.12-slim` — no image build for v0), gated by
  `a2aRelay.enabled`. Reachable through the CF tunnel with a CF Access **bypass** on the host
  (`terraform/contabo/cloudflare.tf`, `a2a_relay_access_enabled`) so machine WSS skips OTP.
- **Session bridge** (`a2a-bridge/wss_bridge.py`): outbound WSS client (auto-reconnect); inbound
  → inbox socket; localhost `127.0.0.1:8760` for the MCP tool's outbound sends. `a2a_mcp.py`
  exposes `a2a_whoami` / `a2a_set_peer` / `a2a_list_peers` / `a2a_send` (peers keyed by session-id).
  Started by the repo `SessionStart` hook (`.claude/settings.json`), cloud-only + `FUZE_A2A_BRIDGE=1`.
- **DevOps env** installs `websockets`/`a2a-sdk`/`mcp` (+ `kubectl`) and sets
  `FUZE_A2A_RELAY_URL`; applied at claude.ai/code (the picker has no API).

## Auth (v0) and hardening

- **v0 = OPEN relay** (no bearer): the capability is the unguessable `cse_…` session-id, plus the
  CF Access bypass. Enough to prove the mechanism; **not** for steady use.
- **Harden (FuzeAgent / FuzeSDLC#219)**: set `FUZE_A2A_RELAY_TOKEN` (relay SealedSecret
  `a2a-relay-secret` + the env), and replace the shared bearer with **per-session minted** tokens
  tied to the agent machine identity. Discovery also graduates from manual id-passing to a
  FuzeAgent rendezvous.

## Deploy / test

1. Merge this change → Argo syncs `a2aRelay` (relay pod + ingress).
2. Merge the Terraform CF Access bypass PR → CD applies → relay reachable without OTP.
3. Update the DevOps env at claude.ai/code with the regenerated `devops.{setup.sh,env}`.
4. Two DevOps sessions: `a2a_whoami` (confirm `relay_connected`), then `a2a_set_peer` + `a2a_send`
   across them; a new turn should appear in the peer. The socket message frame
   (`wss_bridge.py:_inbox_frames`) is the one undocumented bit to confirm live.

Graduates to **FuzeAgent** (cross-provider agent orchestration + rendezvous); it lives here only
to unblock the spike (`agent-templates/README.md`).
