# A2A bridge — cloud↔cloud messaging via the WSS relay

> **⚠️ SUPERSEDED for inbound delivery — do not build on this for new work.**
> The outbound half works, but *inbound* delivery depends on writing frames to the
> peer's internal `CLAUDE_CODE_MESSAGING_SOCKET` (Claude Code's `peerProtocol` v1,
> compiled into `/opt/claude-code/bin/claude`). That frame format is undocumented and
> reverse-engineering it is deliberately guardrailed, so `_inbox_frames` never lands a
> turn — a message reaches the relay but never appears in the peer. Use the **Routines
> API** pattern instead (`create_session` + `create_trigger`/`fire_trigger`), which
> delivers a peer turn natively and even wakes an idle peer, with no relay/socket/token.
> See **`../../orchestration/CAPABILITY_DELEGATION.md`** for the design, the working
> transport, and the fail-closed authorization model. This directory is kept only as the
> record of why the relay/socket route is a dead-end.

Lets one **Claude Code on the web** cloud session message **another** and get a reply —
the direction native cross-session messaging doesn't cover (cross-machine/web is
reply-only and can't open a fresh conversation to a web session).

## Why WSS relay (not a tunnel)

A prior spike proved **cloudflared quick tunnels cannot run in the Anthropic sandbox**:
outbound port **7844** (QUIC + TCP/HTTP2 fallback) is blocked; the only egress is
**HTTPS/443** through the sandbox security proxy to **allowlisted hosts**. So we don't
expose an inbound tunnel — each session dials an **outbound WebSocket over 443** to a
relay we own (`wss://relay.prod.fuzefront.com/ws`, under the allowlisted
`*.fuzefront.com`), and the relay routes by session-id.

```
Session A ──outbound WSS/443──► relay.prod.fuzefront.com ◄──outbound WSS/443── Session B
   register session=A                 route by 'to'                  register session=B
   inbound frame ──► write $CLAUDE_CODE_MESSAGING_SOCKET ──► new turn in A   (confirmed present)
```

## Pieces

- **`wss_bridge.py`** — started by the SessionStart hook. Opens the outbound WSS to the
  relay, registers this session's id, and: (inbound) writes relay frames to the inbox
  socket to start a new turn; (outbound) runs `127.0.0.1:8760` for the MCP tool to submit
  sends, which it forwards up the WS. Uses the `websockets` lib + stdlib.
- **`a2a_mcp.py`** — stdio MCP server (declared in repo `.mcp.json`). Address book keyed
  by **session-id**: `a2a_whoami` (your id + relay-connected), `a2a_set_peer(name, id)`,
  `a2a_list_peers`, `a2a_send(peer, text)`.
- **`start.sh`** — SessionStart hook launcher; cloud-only + `FUZE_A2A_BRIDGE=1`; prints
  this session's id (what a peer needs).
- **`a2a_mcp_launch.sh`** — guarded launcher for the MCP server (opt-in + deps present).

Deps (`websockets` for the daemon, `mcp>=1.9,<2` for the tool server) install in the
**Fuze** and **DevOps** env setup scripts; `FUZE_A2A_BRIDGE=1` + `FUZE_A2A_RELAY_URL` are
set in both. (`a2a-sdk` is intentionally NOT a dep — the desktop bridge imports only
`websockets`, `mcp`, and stdlib; the relay server has its own requirements.) Relay:
`agent-templates/orchestration/a2a_relay/`.

## Test with two DevOps cloud sessions

1. Start two cloud sessions on the **DevOps** env (both clone this repo → get the hook +
   `.mcp.json` + bridge; deps install on first build).
2. In each, run `a2a_whoami` → note `session_id` and confirm `relay_connected: true`.
3. In session B: `a2a_set_peer("A", "<A's session_id>")` then `a2a_send("A", "ping from B")`.
4. A new turn `[A2A from <B-id>] ping from B` should appear in **A**. (If not, the socket
   frame in `wss_bridge.py:_inbox_frames` is the thing to adjust — the auth line is
   documented, the message frame is not.)
5. A replies: `a2a_send("<B-id>", "pong")` (B's id arrived as `reply_to`).

## Limits (v0)

- **Manual id passing.** Durable rendezvous graduates to **FuzeAgent**.
- **Open relay (no bearer).** Capability = the unguessable `cse_…` id + the CF Access
  bypass on the relay host. Turn on `FUZE_A2A_RELAY_TOKEN` (relay SealedSecret + the env)
  to gate it; per-session minted tokens + agent machine identity is the FuzeSDLC#219 path.
- **Socket frame unproven** until the live test starts a turn (the one undocumented bit).
