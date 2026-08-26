#!/usr/bin/env python3
"""A2A WSS relay (v0) — brokers cloud<->cloud Claude Code session messages.

Why this exists: cloudflared quick tunnels CANNOT run in the Anthropic cloud
sandbox (outbound port 7844 is blocked; only HTTPS/443 through the security proxy
to allowlisted hosts is permitted). So instead of exposing an inbound tunnel INTO a
session, each session opens an OUTBOUND WebSocket over 443 to this relay (hosted at
wss://relay.prod.fuzefront.com/ws, under the allowlisted *.fuzefront.com), and the
relay routes messages between sessions by session-id.

Protocol (JSON text frames over the WS):
  connect:  /ws?session=<session-id>[&token=<bearer>]
  send:     {"to":"<peer-session-id>","from":"<my-id>","text":"...","reply_to":"<optional>"}
  routed:   the same frame is delivered verbatim to <to>'s socket
  error:    {"type":"error","error":"...","to":"..."}  (back to sender)

Auth (v0): if env FUZE_A2A_RELAY_TOKEN is set, clients must present a matching
?token=; if unset, the relay runs OPEN — the capability is the unguessable cse_...
session-id. Turn the bearer on (and mint per-session tokens) when this graduates to
FuzeAgent; see README.md.

Dependency-light: only `websockets`. Pinned to 12.0 for a stable API (serve()
handler(websocket) + legacy process_request(path, headers)).
"""
import asyncio
import http
import json
import os
import sys
from urllib.parse import parse_qs, urlparse

import websockets

PORT = int(os.environ.get("PORT", "8000"))
TOKEN = os.environ.get("FUZE_A2A_RELAY_TOKEN", "")  # empty => open mode (v0)

# session_id -> websocket
PEERS = {}


def log(*a):
    print("[a2a-relay]", *a, file=sys.stderr, flush=True)


async def process_request(path, request_headers):
    """Answer the k8s health probe on the same port; let /ws upgrade proceed."""
    if path.startswith("/healthz") or path == "/":
        return (http.HTTPStatus.OK, [("Content-Type", "text/plain")], b"ok\n")
    return None  # proceed with the WebSocket handshake


async def handler(websocket):
    q = parse_qs(urlparse(websocket.path).query)
    session = (q.get("session") or [""])[0]
    token = (q.get("token") or [""])[0]

    if not session:
        await websocket.close(code=4400, reason="missing session")
        return
    if TOKEN and token != TOKEN:
        log(f"reject session={session}: bad token")
        await websocket.close(code=4401, reason="unauthorized")
        return

    prev = PEERS.get(session)
    PEERS[session] = websocket
    if prev is not None and prev is not websocket:
        try:
            await prev.close(code=4409, reason="replaced by newer connection")
        except Exception:  # noqa: BLE001
            pass
    log(f"connect session={session} (peers now {len(PEERS)})")
    try:
        async for raw in websocket:
            await _route(session, raw)
    except websockets.ConnectionClosed:
        pass
    finally:
        if PEERS.get(session) is websocket:
            del PEERS[session]
        log(f"disconnect session={session} (peers now {len(PEERS)})")


async def _route(sender, raw):
    try:
        msg = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return await _err(sender, "bad json", None)
    to = msg.get("to")
    if not to:
        return await _err(sender, "missing 'to'", None)
    msg["from"] = sender  # relay is authoritative on sender identity
    target = PEERS.get(to)
    if target is None:
        return await _err(sender, f"peer {to} not connected", to)
    text_len = len(msg.get("text", "") or "")
    try:
        await target.send(json.dumps(msg))
        log(f"route {sender} -> {to} ({text_len} chars)")
    except Exception as e:  # noqa: BLE001
        await _err(sender, f"delivery failed: {e}", to)


async def _err(sender, error, to):
    ws = PEERS.get(sender)
    if ws is None:
        return
    try:
        await ws.send(json.dumps({"type": "error", "error": error, "to": to}))
    except Exception:  # noqa: BLE001
        pass


async def main():
    log(f"starting on :{PORT} (auth={'bearer' if TOKEN else 'OPEN-v0'})")
    async with websockets.serve(handler, "0.0.0.0", PORT, process_request=process_request,
                                ping_interval=20, ping_timeout=20, max_size=2 ** 20):
        await asyncio.Future()  # run forever


if __name__ == "__main__":
    asyncio.run(main())
