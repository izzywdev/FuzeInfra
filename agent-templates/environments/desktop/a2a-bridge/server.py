#!/usr/bin/env python3
"""A2A bridge — INBOUND half (spike).

Purpose of the spike: prove the two load-bearing unknowns for cloud->cloud
messaging between Claude Code on the web sessions, from *inside* the Anthropic
sandbox:

  1. A sibling process CAN post a message into the running Claude session so a new
     turn starts — via the session inbox socket (CLAUDE_CODE_MESSAGING_SOCKET +
     CLAUDE_CODE_MESSAGING_TOKEN). Documented as supported for a hook/child process
     ("you want a script or hook to post into a session"), incl. where Claude runs
     as PID 1 in a container (the token auth line is how it verifies an own-child).
  2. A `cloudflared` quick tunnel to this server is reachable from another session.

This server is deliberately STDLIB-ONLY (http.server), so the inbound path works
even if the pip installs in the setup script failed. It exposes an A2A-*shaped*
surface (an AgentCard + a message endpoint) — not a spec-complete A2A server. The
durable version adopts the official `a2a-sdk` and graduates discovery/auth to
FuzeAgent; see README.md.

Wire-format caveat (the #1 thing this spike validates): Anthropic documents the
auth line for posting to the socket but NOT the exact message frame. `_inbox_frames`
below is a best-effort guess; if a new turn does not appear, that function is what
to adjust. It logs every byte it sends and the socket's reply.

Run:  PORT=8760 python server.py     (start.sh does this behind the tunnel)
"""
import json
import os
import socket
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(os.environ.get("A2A_BRIDGE_PORT", "8760"))
SOCKET_PATH = os.environ.get("CLAUDE_CODE_MESSAGING_SOCKET", "")
SOCKET_TOKEN = os.environ.get("CLAUDE_CODE_MESSAGING_TOKEN", "")
SESSION_ID = os.environ.get("CLAUDE_CODE_REMOTE_SESSION_ID", "unknown")
AGENT_NAME = os.environ.get("A2A_AGENT_NAME", f"fuze-cloud-session-{SESSION_ID}")


def log(*a):
    print("[a2a-bridge:server]", *a, file=sys.stderr, flush=True)


def _inbox_frames(text, reply_to):
    """The JSON lines written to the inbox socket after the auth line.

    UNDOCUMENTED wire format — this is the spike's primary discovery target. Each
    entry is one newline-delimited JSON object. Adjust here if no new turn appears.
    """
    prefix = f"[A2A from {reply_to}] " if reply_to else "[A2A] "
    return [{"type": "message", "text": prefix + text}]


def post_to_session_inbox(text, reply_to=None):
    """Open the session inbox UDS, send the auth line, then the message frame(s)."""
    if not SOCKET_PATH:
        raise RuntimeError("CLAUDE_CODE_MESSAGING_SOCKET is unset — not in a "
                           "messaging-enabled Claude Code session?")
    path = SOCKET_PATH[4:] if SOCKET_PATH.startswith("uds:") else SOCKET_PATH
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(5.0)
    s.connect(path)
    try:
        # Auth line first. Optional on Linux, but required for own-child
        # verification when Claude is PID 1 (the cloud sandbox case).
        auth = json.dumps({"type": "auth", "token": SOCKET_TOKEN}) + "\n"
        s.sendall(auth.encode("utf-8"))
        log("sent auth line")
        for frame in _inbox_frames(text, reply_to):
            line = json.dumps(frame) + "\n"
            s.sendall(line.encode("utf-8"))
            log("sent frame:", line.strip())
        # Best-effort read of any acknowledgement, for the transcript.
        try:
            reply = s.recv(4096)
            if reply:
                log("socket replied:", reply.decode("utf-8", "replace").strip())
        except socket.timeout:
            log("no socket reply within timeout (may be normal)")
    finally:
        s.close()


AGENT_CARD = {
    "name": AGENT_NAME,
    "description": "Claude Code cloud session, reachable via the FuzeInfra A2A bridge (spike).",
    "version": "0.0.1-spike",
    "protocolVersion": "0.3",
    "capabilities": {"streaming": False},
    "skills": [{
        "id": "relay-message",
        "name": "Relay a message into the running Claude session",
        "description": "Delivers the message text as a new turn in this cloud session.",
    }],
    # url is filled in per-request from the public tunnel host when known.
    "url": os.environ.get("A2A_PUBLIC_URL", f"http://localhost:{PORT}/"),
}


class Handler(BaseHTTPRequestHandler):
    def _json(self, code, obj):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):  # quiet the default noisy logger; we use log()
        pass

    def do_GET(self):
        if self.path in ("/.well-known/agent-card.json", "/.well-known/agent.json"):
            return self._json(200, AGENT_CARD)
        if self.path == "/health":
            return self._json(200, {"ok": True, "session": SESSION_ID})
        return self._json(404, {"error": "not found"})

    def do_POST(self):
        n = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(n) if n else b""
        try:
            payload = json.loads(raw or b"{}")
        except json.JSONDecodeError as e:
            return self._json(400, {"error": f"bad json: {e}"})

        # Accept both a plain {text, reply_to} body and an A2A-shaped
        # JSON-RPC message/send envelope, so `curl` and an a2a-sdk client both work.
        text, reply_to = _extract(payload)
        if not text:
            return self._json(400, {"error": "no message text found in body"})
        log(f"inbound message ({len(text)} chars) reply_to={reply_to!r}")
        try:
            post_to_session_inbox(text, reply_to)
        except Exception as e:  # noqa: BLE001 — spike: surface the failure verbatim
            log("INBOX INJECTION FAILED:", repr(e))
            return self._json(502, {"ok": False, "error": str(e)})
        return self._json(200, {"ok": True, "delivered_to": SESSION_ID, "at": time.time()})


def _extract(payload):
    """Pull (text, reply_to) from either a plain body or an A2A message/send envelope."""
    if "text" in payload:
        return payload.get("text"), payload.get("reply_to")
    params = payload.get("params", payload)
    msg = params.get("message", params)
    reply_to = params.get("reply_to") or payload.get("reply_to")
    parts = msg.get("parts") if isinstance(msg, dict) else None
    if isinstance(parts, list):
        texts = [p.get("text", "") for p in parts if isinstance(p, dict) and p.get("kind", p.get("type")) in (None, "text")]
        joined = " ".join(t for t in texts if t).strip()
        if joined:
            return joined, reply_to
    if isinstance(msg, dict) and msg.get("text"):
        return msg["text"], reply_to
    return None, reply_to


def main():
    if not SOCKET_PATH:
        log("WARNING: CLAUDE_CODE_MESSAGING_SOCKET unset — inbound injection will fail. "
            "This is expected outside a messaging-enabled session.")
    log(f"session={SESSION_ID} port={PORT} socket={SOCKET_PATH or '(none)'}")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
