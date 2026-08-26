#!/usr/bin/env python3
"""A2A bridge — OUTBOUND MCP tools (WSS relay edition).

MCP is pull-only (a server can't inject a turn), so directions are split: inbound is
wss_bridge.py writing to the inbox socket; outbound is these MCP tools, which hand a
message to the local wss_bridge (127.0.0.1:$A2A_BRIDGE_PORT) to forward up the WS to
the relay, which routes it to the peer session.

Address book (manual for the spike): peers are keyed by the peer's SESSION-ID
(CLAUDE_CODE_REMOTE_SESSION_ID / cse_...), which you paste from the peer's start log.
Durable discovery graduates to a FuzeAgent rendezvous.

Pinned mcp>=1.9,<2 (mcp 2.0 removed mcp.server.fastmcp). HTTP via stdlib urllib.
"""
import json
import os
import urllib.request

from mcp.server.fastmcp import FastMCP

SESSION_ID = os.environ.get("CLAUDE_CODE_REMOTE_SESSION_ID", "unknown")
BRIDGE = f"http://127.0.0.1:{os.environ.get('A2A_BRIDGE_PORT', '8760')}"

mcp = FastMCP("fuze-a2a")


def _state_dir():
    d = os.environ.get("A2A_BRIDGE_STATE") or os.path.join(
        os.environ.get("TMPDIR", "/tmp"), "a2a-bridge")
    os.makedirs(d, exist_ok=True)
    return d


def _status():
    try:
        with open(os.path.join(_state_dir(), "status.json"), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {"session_id": SESSION_ID, "connected": False}


def _peers_path():
    return os.path.join(_state_dir(), "peers.json")


def _load_peers():
    try:
        with open(_peers_path(), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def _save_peers(p):
    with open(_peers_path(), "w", encoding="utf-8") as f:
        json.dump(p, f, indent=2)


def _resolve(peer):
    """A stored peer name -> its session-id; a cse_/session_ id -> itself."""
    if peer.startswith("cse_") or peer.startswith("session_"):
        return peer
    return _load_peers().get(peer)


def _post(path, body, timeout=15):
    req = urllib.request.Request(BRIDGE + path, data=json.dumps(body).encode(),
                                 method="POST", headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 — localhost bridge
        return r.status, r.read().decode("utf-8", "replace")


@mcp.tool()
def a2a_whoami() -> str:
    """GET this cloud session's own A2A identity — its session-id (hand this to a peer
    so it can message you) and whether the bridge is connected to the relay."""
    st = _status()
    return json.dumps({"session_id": SESSION_ID, "relay_connected": st.get("connected", False),
                       "relay_url": st.get("relay_url", "")})


@mcp.tool()
def a2a_set_peer(name: str, session_id: str) -> str:
    """SET (register) a peer under a short name so you can send by name. `session_id` is
    the peer's cse_... id (from its a2a_whoami / start log)."""
    peers = _load_peers()
    peers[name] = session_id
    _save_peers(peers)
    return json.dumps({"ok": True, "peers": peers})


@mcp.tool()
def a2a_list_peers() -> str:
    """List registered peers and this session's own identity/relay status."""
    return json.dumps({"self": _status(), "peers": _load_peers()})


@mcp.tool()
def a2a_send(peer: str, text: str, reply_to: str = "") -> str:
    """Deliver a message to a peer session via the relay.

    peer:     a name from a2a_set_peer, OR a raw cse_/session_ id.
    text:     the message to deliver into the peer's session (starts a new turn there).
    reply_to: where the peer should reply (defaults to this session's own id so replies
              route back here). Pass another id to redirect the reply.
    """
    to = _resolve(peer)
    if not to:
        return json.dumps({"ok": False, "error": f"unknown peer {peer!r}; a2a_set_peer first",
                           "known": list(_load_peers())})
    if not reply_to:
        reply_to = SESSION_ID
    try:
        status, body = _post("/", {"to": to, "text": text, "reply_to": reply_to})
        return json.dumps({"ok": 200 <= status < 300, "status": status, "response": body})
    except Exception as e:  # noqa: BLE001
        return json.dumps({"ok": False, "error": str(e),
                           "hint": "is wss_bridge.py running and connected? check a2a_whoami"})


if __name__ == "__main__":
    mcp.run()  # stdio transport
