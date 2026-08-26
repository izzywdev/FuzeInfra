# A2A bridge (spike) — cloud↔cloud messaging for Claude Code sessions

Lets one **Claude Code on the web** cloud session send a message into **another** cloud
session and get a reply — the direction native cross-session messaging does **not** cover
(cross-machine/web is reply-only and can't open a fresh conversation to a web session).

This is a **spike**: it exists to prove two things that can only be verified inside a real
Anthropic sandbox, before we invest in the durable version:

1. A sibling process can post to the session **inbox socket** (`CLAUDE_CODE_MESSAGING_SOCKET`
   + `CLAUDE_CODE_MESSAGING_TOKEN`) and make a **new turn appear** in the running session.
2. A `cloudflared` **quick tunnel** to a local server is reachable from another session
   under the sandbox security proxy.

## How it works

```
Session B                                   Session A
─────────                                   ─────────
Claude calls a2a_send("A", "hi")            server.py  (behind cloudflared quick tunnel)
   │  (MCP, outbound — a2a_mcp.py)             ▲  POST { text, reply_to }
   └──────── HTTPS to A's tunnel URL ──────────┘
                                            server.py writes to $CLAUDE_CODE_MESSAGING_SOCKET
                                               → a new turn appears in A  (inbound)
A replies with a2a_send("B", "...") ── back to B's tunnel the same way (reply_to)
```

- **Inbound** (`server.py`) — stdlib-only HTTP server (no pip deps, so it works even if
  installs failed). Serves an A2A-shaped AgentCard at `/.well-known/agent-card.json` and
  accepts a message on `POST /`, then writes it to the inbox socket. **MCP is pull-only and
  cannot inject a turn**, which is why inbound uses the socket, not MCP.
- **Outbound** (`a2a_mcp.py`) — a stdio **MCP** server Claude calls to send. Address book:
  `a2a_whoami` (get this session's URL), `a2a_set_peer(name,url)` (register a peer),
  `a2a_list_peers`, `a2a_send(peer,text)`, `a2a_card(peer)`.
- **`start.sh`** — the SessionStart hook (`.claude/settings.json`). Cloud-only + opt-in
  (`CLAUDE_CODE_REMOTE=true` and `FUZE_A2A_BRIDGE=1`); starts the tunnel + server and prints
  the public URL. Deps (`cloudflared`, `a2a-sdk`, `mcp`) are installed by the **DevOps** env
  setup script (baked into the snapshot; the setup script runs only on the first, uncached
  session, so per-session start lives in the hook, not the setup script).

## Run the spike (two DevOps cloud sessions)

1. Start two cloud sessions on the **DevOps** environment (it has `*.cloudflare.com` +
   `*.trycloudflare.com` egress and installs the deps). Each prints its public URL at start.
2. In session B: `a2a_set_peer("A", "<A's printed URL>")` then `a2a_send("A", "ping from B")`.
3. **Validation 1 + 2**: a new turn `[A2A from …] ping from B` appears in **A**. (If not, the
   socket wire-frame in `server.py:_inbox_frames` is the thing to adjust — it's the one
   undocumented bit; the auth line is documented, the message frame is not.)
4. In A: `a2a_send("B", "pong")` (B's URL arrived as `reply_to`, so `a2a_set_peer` isn't even
   needed) → reply appears in B.
5. Quick manual check without MCP: `curl -s <A-url>/health` and
   `curl -sX POST <A-url>/ -d '{"text":"hi"}'`.

**Reply to a human/local origin:** `reply_to` can be any HTTPS webhook, not just a peer
tunnel — e.g. a Telegram bridge — so a cloud session can answer a human. Durable
human-channel routing is a FuzeAgent concern (below).

## Limitations / what's deliberately deferred

- **Manual URL passing.** Ephemeral `trycloudflare.com` URLs change per session; you paste
  them. The durable **rendezvous/registry** (session-id → URL) and cross-provider
  orchestration belong in **FuzeAgent** (see `../../README.md`), not FuzeInfra.
- **Unauthenticated endpoint.** The quick-tunnel URL is a weak capability URL with no auth —
  fine for a spike, not for steady use. Cloud envs have **no secret store** (env vars are
  world-readable), so real auth (CF Access service token / minted per-session bearer) is a
  design item for the FuzeAgent version.
- **Not spec-complete A2A.** `server.py` is A2A-*shaped*; the durable version adopts the
  official [`a2a-sdk`](https://github.com/a2aproject/a2a-python) (installed here already).
