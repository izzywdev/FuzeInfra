#!/usr/bin/env python3
"""A2A bridge — OUTBOUND half (spike): a stdio MCP server Claude calls to SEND.

MCP is pull-only — a server cannot inject a conversation turn — so the two
directions are split: inbound is the socket-writing HTTP server (server.py);
outbound is this MCP server. Declared in the repo's `.mcp.json`; Claude connects
to it over stdio.

Address book (what the operator asked for), manual for the spike:
  a2a_whoami()            -> GET this session's own tunnel URL + id, to hand to peers.
  a2a_set_peer(name, url) -> SET another session's URL under a short name.
  a2a_list_peers()        -> list registered peers (+ this session's own URL).
  a2a_send(peer, text)    -> deliver a message to a peer (by name OR raw URL).
  a2a_card(peer)          -> fetch a peer's AgentCard to confirm reachability.
Peers persist in the bridge state dir so they survive across tool calls / restarts.
The durable version resolves names via a FuzeAgent rendezvous instead of manual set.

Pinned `mcp>=1.9,<2`: mcp 2.0 removed `mcp.server.fastmcp` (this bit handoff-mcp —
see agent-templates/orchestration/handoff_mcp/requirements.txt). HTTP via stdlib
urllib, so the only added dep is `mcp`.
"""
import json
import os
import urllib.request

from mcp.server.fastmcp import FastMCP

SESSION_ID = os.environ.get("CLAUDE_CODE_REMOTE_SESSION_ID", "unknown")

mcp = FastMCP("fuze-a2a")


def _state_dir():
    d = os.environ.get("A2A_BRIDGE_STATE") or os.path.join(
        os.environ.get("TMPDIR", "/tmp"), "a2a-bridge")
    os.makedirs(d, exist_ok=True)
    return d


def _public_url():
    """This session's tunnel URL. This MCP server is a separate process from
    start.sh, so prefer the env var but fall back to the file start.sh writes (the
    URL is only known once the ephemeral tunnel is up)."""
    env = os.environ.get("A2A_PUBLIC_URL", "")
    if env:
        return env
    try:
        with open(os.path.join(_state_dir(), "public_url"), encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


def _peers_path():
    return os.path.join(_state_dir(), "peers.json")


def _load_peers():
    try:
        with open(_peers_path(), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def _save_peers(peers):
    with open(_peers_path(), "w", encoding="utf-8") as f:
        json.dump(peers, f, indent=2)


def _resolve(peer):
    """A stored peer name -> its URL; a raw http(s) URL -> itself."""
    if peer.startswith("http://") or peer.startswith("https://"):
        return peer
    return _load_peers().get(peer)


def _post(url, body, timeout=15):
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 — operator-supplied peer URL (spike)
        return r.status, r.read().decode("utf-8", "replace")


@mcp.tool()
def a2a_whoami() -> str:
    """GET this cloud session's own A2A identity — its public tunnel URL and session
    id. Hand the URL to another session so it can talk to this one. An empty URL means
    the tunnel is not up yet (check the bridge start log / state dir)."""
    return json.dumps({"session_id": SESSION_ID, "public_url": _public_url()})


@mcp.tool()
def a2a_set_peer(name: str, url: str) -> str:
    """SET (register) another session's address under a short name, so you can send to
    it by name later. `url` is the peer's tunnel base URL (from its a2a_whoami), or any
    HTTPS webhook a human/local origin controls."""
    peers = _load_peers()
    peers[name] = url.rstrip("/")
    _save_peers(peers)
    return json.dumps({"ok": True, "peers": peers})


@mcp.tool()
def a2a_list_peers() -> str:
    """List registered peers and this session's own public URL."""
    return json.dumps({"self": {"session_id": SESSION_ID, "public_url": _public_url()},
                       "peers": _load_peers()})


@mcp.tool()
def a2a_send(peer: str, text: str, reply_to: str = "") -> str:
    """Deliver a message to a peer's Claude session.

    peer:     a name registered with a2a_set_peer, OR a raw https:// tunnel URL.
    text:     the message to deliver into the peer's session (starts a new turn there).
    reply_to: where the peer should reply (defaults to THIS session's own public URL,
              so replies come back here). Pass a different URL/webhook to route a reply
              to a human/local origin instead.
    """
    url = _resolve(peer)
    if not url:
        return json.dumps({"ok": False, "error": f"unknown peer {peer!r}; a2a_set_peer first",
                           "known": list(_load_peers())})
    if not reply_to:
        reply_to = _public_url()
    try:
        status, body = _post(url.rstrip("/") + "/", {"text": text, "reply_to": reply_to})
        return json.dumps({"ok": 200 <= status < 300, "status": status, "response": body})
    except Exception as e:  # noqa: BLE001 — spike: report the failure to Claude verbatim
        return json.dumps({"ok": False, "error": str(e), "target": url})


@mcp.tool()
def a2a_card(peer: str) -> str:
    """Fetch a peer's AgentCard (GET /.well-known/agent-card.json) to confirm it is
    reachable before sending. `peer` is a registered name or a raw URL."""
    url = _resolve(peer)
    if not url:
        return json.dumps({"ok": False, "error": f"unknown peer {peer!r}"})
    try:
        with urllib.request.urlopen(url.rstrip("/") + "/.well-known/agent-card.json", timeout=15) as r:  # noqa: S310
            return r.read().decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001
        return json.dumps({"ok": False, "error": str(e), "target": url})


if __name__ == "__main__":
    mcp.run()  # stdio transport
