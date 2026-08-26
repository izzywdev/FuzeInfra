#!/usr/bin/env python3
"""A2A session bridge (WSS) — replaces the dead cloudflared tunnel.

cloudflared quick tunnels can't run in the Anthropic sandbox (port 7844 blocked;
only HTTPS/443 through the security proxy to allowlisted hosts). So instead of an
inbound tunnel, this dials an OUTBOUND WebSocket over 443 to the relay we own
(FUZE_A2A_RELAY_URL, under the allowlisted *.fuzefront.com) and registers by
session-id. The relay routes messages between sessions.

Two directions:
  INBOUND  relay frame {from,text,reply_to} -> write to $CLAUDE_CODE_MESSAGING_SOCKET
           so a new turn starts in THIS session. (The socket message FRAME is the one
           undocumented bit — see _inbox_frames; verbose logging, best-effort.)
  OUTBOUND a2a_mcp.py POSTs {to,text,reply_to} to 127.0.0.1:$A2A_BRIDGE_PORT, and this
           forwards it up the WS to the relay, which routes it to the peer.

Dependency-light: `websockets` (added to the DevOps env) + stdlib. Started by the
SessionStart hook (start.sh), cloud-only + opt-in (FUZE_A2A_BRIDGE=1).
"""
import asyncio
import json
import os
import socket
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlencode

import websockets

RELAY_URL = os.environ.get("FUZE_A2A_RELAY_URL", "")
RELAY_TOKEN = os.environ.get("FUZE_A2A_RELAY_TOKEN", "")
SESSION_ID = os.environ.get("CLAUDE_CODE_REMOTE_SESSION_ID", "unknown")
HTTP_PORT = int(os.environ.get("A2A_BRIDGE_PORT", "8760"))
SOCKET_PATH = os.environ.get("CLAUDE_CODE_MESSAGING_SOCKET", "")
SOCKET_TOKEN = os.environ.get("CLAUDE_CODE_MESSAGING_TOKEN", "")
STATE = os.environ.get("A2A_BRIDGE_STATE") or os.path.join(
    os.environ.get("TMPDIR", "/tmp"), "a2a-bridge")

_loop = None            # the asyncio loop (set in main)
_outbound = None        # asyncio.Queue of frames to send up the WS
_connected = {"v": False}


def log(*a):
    print("[a2a-bridge:wss]", *a, file=sys.stderr, flush=True)


def _write_status():
    try:
        os.makedirs(STATE, exist_ok=True)
        with open(os.path.join(STATE, "status.json"), "w", encoding="utf-8") as f:
            json.dump({"session_id": SESSION_ID, "relay_url": RELAY_URL,
                       "connected": _connected["v"]}, f)
    except OSError:
        pass


# --- INBOUND: write a delivered message into this session -------------------------
def _inbox_frames(text, reply_to):
    """JSON lines written to the inbox socket after the auth line. UNDOCUMENTED wire
    format — this is the one remaining unknown; adjust here if no new turn appears."""
    prefix = f"[A2A from {reply_to}] " if reply_to else "[A2A] "
    return [{"type": "message", "text": prefix + text}]


def post_to_session_inbox(text, reply_to=None):
    if not SOCKET_PATH:
        raise RuntimeError("CLAUDE_CODE_MESSAGING_SOCKET unset")
    path = SOCKET_PATH[4:] if SOCKET_PATH.startswith("uds:") else SOCKET_PATH
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(5.0)
    s.connect(path)
    try:
        s.sendall((json.dumps({"type": "auth", "token": SOCKET_TOKEN}) + "\n").encode())
        for frame in _inbox_frames(text, reply_to):
            s.sendall((json.dumps(frame) + "\n").encode())
            log("wrote inbox frame:", json.dumps(frame))
        try:
            reply = s.recv(4096)
            if reply:
                log("inbox socket replied:", reply.decode("utf-8", "replace").strip())
        except socket.timeout:
            log("no inbox socket reply within timeout (may be normal)")
    finally:
        s.close()


def _handle_inbound(msg):
    text = msg.get("text", "")
    if not text:
        return
    frm = msg.get("from")
    reply_to = msg.get("reply_to") or frm
    log(f"inbound from={frm} ({len(text)} chars)")
    try:
        post_to_session_inbox(text, reply_to)
    except Exception as e:  # noqa: BLE001
        log("INBOX INJECTION FAILED:", repr(e))


# --- OUTBOUND: localhost HTTP that a2a_mcp posts to ------------------------------
class _Handler(BaseHTTPRequestHandler):
    def _json(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path == "/health":
            return self._json(200, {"ok": True, "session": SESSION_ID,
                                    "connected": _connected["v"]})
        return self._json(404, {"error": "not found"})

    def do_POST(self):
        n = int(self.headers.get("Content-Length", "0") or "0")
        try:
            payload = json.loads(self.rfile.read(n) or b"{}")
        except json.JSONDecodeError as e:
            return self._json(400, {"error": f"bad json: {e}"})
        to = payload.get("to")
        text = payload.get("text")
        if not to or not text:
            return self._json(400, {"error": "need 'to' and 'text'"})
        if not _connected["v"]:
            return self._json(503, {"ok": False, "error": "relay not connected"})
        frame = {"to": to, "text": text, "reply_to": payload.get("reply_to") or ""}
        try:
            fut = asyncio.run_coroutine_threadsafe(_outbound.put(frame), _loop)
            fut.result(timeout=5)
        except Exception as e:  # noqa: BLE001
            return self._json(502, {"ok": False, "error": f"enqueue failed: {e}"})
        return self._json(200, {"ok": True, "to": to})


def _start_http():
    ThreadingHTTPServer(("127.0.0.1", HTTP_PORT), _Handler).serve_forever()


# --- WS client: connect, register, pump both directions -------------------------
async def _ws_session(url):
    async with websockets.connect(url, ping_interval=20, ping_timeout=20,
                                  max_size=2 ** 20) as ws:
        _connected["v"] = True
        _write_status()
        log(f"connected to relay as session={SESSION_ID}")

        async def sender():
            while True:
                frame = await _outbound.get()
                await ws.send(json.dumps(frame))
                log(f"sent -> {frame.get('to')}")

        send_task = asyncio.create_task(sender())
        try:
            async for raw in ws:
                try:
                    msg = json.loads(raw)
                except (json.JSONDecodeError, TypeError):
                    continue
                if msg.get("type") == "error":
                    log("relay error:", msg.get("error"))
                    continue
                await asyncio.get_running_loop().run_in_executor(None, _handle_inbound, msg)
        finally:
            send_task.cancel()


async def main():
    global _loop, _outbound
    if not RELAY_URL:
        log("FUZE_A2A_RELAY_URL unset — nothing to do"); return
    _loop = asyncio.get_running_loop()
    _outbound = asyncio.Queue()
    threading.Thread(target=_start_http, daemon=True).start()
    log(f"session={SESSION_ID} relay={RELAY_URL} http=127.0.0.1:{HTTP_PORT} "
        f"socket={SOCKET_PATH or '(none)'}")

    qs = {"session": SESSION_ID}
    if RELAY_TOKEN:
        qs["token"] = RELAY_TOKEN
    url = RELAY_URL + ("&" if "?" in RELAY_URL else "?") + urlencode(qs)

    backoff = 1
    while True:
        try:
            await _ws_session(url)
        except Exception as e:  # noqa: BLE001
            log("ws session ended:", repr(e))
        _connected["v"] = False
        _write_status()
        await asyncio.sleep(backoff)
        backoff = min(backoff * 2, 30)


if __name__ == "__main__":
    asyncio.run(main())
