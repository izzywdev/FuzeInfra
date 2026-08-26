# Cloud↔cloud A2A messaging for Claude Code sessions

**Status:** spike (2026-08-26). Code: `agent-templates/environments/desktop/a2a-bridge/`.

## Goal

Let one Claude Code **cloud** session (Claude Code on the web) send a message into **another
cloud session** and get a reply — **cloud → cloud, 2-way** — bootstrapped inside the
Anthropic sandbox from the environment's setup, exposed over a Cloudflare tunnel. The reply
may go back to another cloud session **or to a human/local origin**.

## Why build (native doesn't cover this)

Native cross-session messaging (`SendMessage`/`ListAgents`) is **not** a cloud→cloud path:

- Reaching cloud sessions is gated on a *local* session being connected to **Remote Control**;
  a cloud session is not that.
- Cross-machine/web delivery is **reply-only** — you "cannot open a fresh conversation with a
  session reachable only through the web," and each cloud session is its own isolated container
  (they can't share the on-disk inbox socket the way two sessions in one container can).

So the bridge supplies the missing capability. (An earlier draft of this doc described
*local → cloud*, one-way, via the native feature — that was the wrong direction and is replaced.)

## Three mechanics that shape the design

1. **The setup script runs only on the first, uncached session** (then the FS snapshot is reused
   and the script is skipped). → **Setup script = install** (`a2a-sdk`, `mcp`, `cloudflared`, baked
   into the snapshot); a repo-committed **`SessionStart` hook** (`CLAUDE_CODE_REMOTE`-scoped) =
   **start** the tunnel + bridge every session. Wiring: `.claude/settings.json`.
2. **MCP is pull-only** — a server can't inject a conversation turn. → **Inbound = the inbox
   socket** (`CLAUDE_CODE_MESSAGING_SOCKET` + `CLAUDE_CODE_MESSAGING_TOKEN`; documented to work for
   an own-child process even where Claude is PID 1, via the token auth line). **Outbound/reply = an
   MCP tool** (`a2a_send`). Wiring: `.mcp.json`.
3. **No secret store; env vars world-readable; tunnel URL ephemeral.** → an ephemeral
   `trycloudflare.com` **quick tunnel** (no token). Discovery is **manual URL passing** for the
   spike; the durable rendezvous + real auth graduate to **FuzeAgent**.

## Prerequisite: network allowlist

The bridge rides the **DevOps** cloud env, which the allowlist PR extended with `*.cloudflare.com`
(cloudflared + `pkg.cloudflare.com` for the apt install), `*.trycloudflare.com` / `*.argotunnel.com`
(the tunnel), and `*.fuzefront.com`. `cloudflared` installs via Cloudflare's **apt repo**, not a
GitHub release — the session proxy 403s release assets for repos not attached to the session.

## Adopt vs build

- **Adopt** the official [`a2a-sdk`](https://github.com/a2aproject/a2a-python) for AgentCard +
  client/server (installed in the env). The spike's `server.py` is deliberately stdlib-only and
  A2A-*shaped* so the inbound path works even if pip installs fail; the durable version swaps in
  a2a-sdk proper.
- **Build** only the thin bridge glue (socket writer + MCP address-book + launcher).

## The spike, and what it proves

Two unknowns are only answerable inside a real sandbox — the spike exists to answer them:

1. A sibling process posting to the inbox socket actually **starts a new turn** in-session.
2. A `trycloudflare` **quick tunnel is reachable** under the sandbox security proxy.

Run steps and the tool list are in `agent-templates/environments/desktop/a2a-bridge/README.md`.
If (1)/(2) hold, promote discovery/auth/orchestration to FuzeAgent.

## Out of scope now → FuzeAgent

Durable rendezvous/registry (session-id → URL), cross-provider agent orchestration, authenticated
inbound (CF Access service token / minted per-session bearer), and human-channel reply routing
(Telegram / Remote Control). `agent-templates/README.md` already states this agent-orchestration
layer belongs in FuzeAgent, not FuzeInfra.
