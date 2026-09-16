#!/bin/bash
# Fuze — Claude Code cloud environment setup script.
# General FuzeOne agentic-dev environment: the shared toolchain every domain environment builds on.
#
# GENERATED from agent-templates/environments/cloud-fuze.json by
# agent-templates/environments/desktop/render.py — do not edit by hand.
# Paste into the Setup script field of the environment dialog at claude.ai/code.
#
# Must exit 0: a non-zero exit makes the session fail to start, so every install
# is || true. Independent installs run in parallel to stay under the ~5 min budget.
set -u

# apt is serialised — dpkg holds a global lock, so parallel installs deadlock.
echo "[setup] apt"
apt-get update -qq || true
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq gh || true

# Independent of each other — run concurrently, then wait.
( echo "[setup] pip"; pip install --quiet --no-input pytest pytest-asyncio requests httpx pyyaml yamllint check-jsonschema 'mcp>=1.9,<2' websockets || pip install --quiet --no-input --break-system-packages pytest pytest-asyncio requests httpx pyyaml yamllint check-jsonschema 'mcp>=1.9,<2' websockets || pip install --quiet --no-input --break-system-packages --ignore-installed pytest pytest-asyncio requests httpx pyyaml yamllint check-jsonschema 'mcp>=1.9,<2' websockets || true ) &
( echo "[setup] npm"; npm install -g --silent prettier || true ) &
( echo "[setup] go github.com/yannh/kubeconform/cmd/kubeconform@latest"; GOBIN=/usr/local/bin go install github.com/yannh/kubeconform/cmd/kubeconform@latest || true ) &
( echo "[setup] helm"
  HELM_VER="$(curl -fsSL https://get.helm.sh/helm-latest-version 2>/dev/null | tr -d '[:space:]')"
  if [ -n "${HELM_VER:-}" ] \
     && curl -fsSL "https://get.helm.sh/helm-${HELM_VER}-linux-amd64.tar.gz" -o /tmp/helm.tgz \
     && tar -xzf /tmp/helm.tgz -C /tmp linux-amd64/helm; then
    install -m 0755 /tmp/linux-amd64/helm /usr/local/bin/helm || true
  else
    echo "[setup] helm: get.helm.sh unavailable, building from source" >&2
    GOBIN=/usr/local/bin go install helm.sh/helm/v3/cmd/helm@latest || true
  fi ) &
wait

# --- A2A cloud<->cloud bridge: env-level launcher (repo-independent) ---
# The bridge daemon otherwise starts only when FuzeInfra is the checked-out repo (its
# repo-level SessionStart hook). Drop a stable copy + a USER-level hook so it starts for
# EVERY cloud session in this env. start.sh is self-guarded (no-op unless CLAUDE_CODE_REMOTE
# =true AND FUZE_A2A_BRIDGE=1) and idempotent (skips if bridge.pid is live), so it safely
# coexists with the repo-level hook — double-invocation is a no-op. See docs/cloud-a2a-bridge.md.
echo "[setup] a2a-bridge (env-level launcher)"
install -d -m 0755 /opt/fuze/a2a-bridge || true
cat > /opt/fuze/a2a-bridge/start.sh <<'__A2A_FILE_START_SH__'
#!/bin/bash
# A2A bridge launcher — invoked by the repo SessionStart hook (.claude/settings.json).
#
# Cloud-only + opt-in: no-ops unless CLAUDE_CODE_REMOTE=true AND FUZE_A2A_BRIDGE=1
# (set in the DevOps env). Starts wss_bridge.py, which opens an OUTBOUND WebSocket to
# the relay ($FUZE_A2A_RELAY_URL) — no inbound tunnel, because the sandbox blocks
# everything except HTTPS/443 to allowlisted hosts (cloudflared's 7844 is denied).
#
# Prints this session's id — that id is what a peer needs to message this session.
# Daemon is detached so it survives the hook returning; the hook must not block.
set -u

[ "${CLAUDE_CODE_REMOTE:-}" = "true" ] || { echo "[a2a-bridge] not a cloud session; skip"; exit 0; }
[ "${FUZE_A2A_BRIDGE:-}" = "1" ]      || { echo "[a2a-bridge] FUZE_A2A_BRIDGE!=1; skip"; exit 0; }

HERE="$(cd "$(dirname "$0")" && pwd)"
STATE="${A2A_BRIDGE_STATE:-${TMPDIR:-/tmp}/a2a-bridge}"
mkdir -p "$STATE"

# Idempotent: SessionStart fires on startup AND resume — don't double-start.
if [ -f "$STATE/bridge.pid" ] && kill -0 "$(cat "$STATE/bridge.pid" 2>/dev/null)" 2>/dev/null; then
  echo "[a2a-bridge] already running (pid $(cat "$STATE/bridge.pid"))"
  exit 0
fi

# Dependency backstop. The env Setup script installs the bridge deps at BUILD time, but a
# session on a stale/cached env snapshot (or a partial install) can boot missing them —
# `websockets` (this daemon) or `mcp` (the MCP tool server) — which surfaces only as a
# dead bridge / CONNECTION_CLOSED with no obvious cause. Install here at hook time as the
# earliest recovery point, with the same 3-tier escalation the setup uses: the last retry
# adds --ignore-installed because mcp pulls a newer PyJWT than the distro-managed one,
# which pip cannot uninstall ("RECORD file not found ... installed by debian"). Best-effort
# and logged to $STATE/bridge_deps.log; if PyPI is unreachable at hook time it is a no-op
# and the build-time install remains the durable path.
DEPLOG="$STATE/bridge_deps.log"
ensure_pymod() {  # <import-name> <pip-spec>
  python3 -c "import $1" 2>/dev/null && return 0
  echo "[deps $(date -u +%H:%M:%S)] import $1 failed — installing $2" >>"$DEPLOG"
  pip install --quiet --no-input "$2" >>"$DEPLOG" 2>&1 \
    || pip install --quiet --no-input --break-system-packages "$2" >>"$DEPLOG" 2>&1 \
    || pip install --quiet --no-input --break-system-packages --ignore-installed "$2" >>"$DEPLOG" 2>&1 \
    || true
  python3 -c "import $1" 2>/dev/null
}

# The daemon needs `websockets` — ensure it synchronously before launching.
ensure_pymod websockets 'websockets' \
  || echo "[a2a-bridge] WARNING: 'websockets' still unavailable; bridge may fail to start" >&2
# The MCP tool server needs `mcp` — kick its install in the background so a2a_mcp_launch.sh
# likely finds it ready (that launcher also self-heals as a fallback). Non-blocking so the
# hook does not stall session start on mcp's larger dependency tree.
( ensure_pymod 'mcp.server.fastmcp' 'mcp>=1.9,<2' >/dev/null 2>&1 || true ) &

setsid nohup python3 "$HERE/wss_bridge.py" >"$STATE/bridge.log" 2>&1 &
echo $! >"$STATE/bridge.pid"

# Register with the delivery gateway so peers can reach THIS session by name/id — even
# after it goes idle (the gateway wakes it via `claude -p --cloud`). Best-effort.
if [ -n "${FUZE_A2A_GATEWAY_URL:-}" ] && [ -n "${CLAUDE_CODE_REMOTE_SESSION_ID:-}" ]; then
  curl -sS -m 10 -X POST "${FUZE_A2A_GATEWAY_URL%/}/register" \
    -H "Content-Type: application/json" \
    ${FUZE_A2A_GATEWAY_TOKEN:+-H "Authorization: Bearer ${FUZE_A2A_GATEWAY_TOKEN}"} \
    -d "{\"name\":\"${FUZE_AGENT_NAME:-$CLAUDE_CODE_REMOTE_SESSION_ID}\",\"session_id\":\"$CLAUDE_CODE_REMOTE_SESSION_ID\"}" \
    >"$STATE/register.log" 2>&1 || true
fi

echo "======================================================================"
echo " A2A bridge starting for session: ${CLAUDE_CODE_REMOTE_SESSION_ID:-unknown}"
echo " Relay: ${FUZE_A2A_RELAY_URL:-<FUZE_A2A_RELAY_URL unset>}"
echo " To talk to THIS session, a peer runs:  a2a_set_peer <name> ${CLAUDE_CODE_REMOTE_SESSION_ID:-<id>}"
echo " Then check connection with the a2a_whoami MCP tool. Logs: $STATE/bridge.log"
echo "======================================================================"
exit 0
__A2A_FILE_START_SH__
cat > /opt/fuze/a2a-bridge/wss_bridge.py <<'__A2A_FILE_WSS_BRIDGE_PY__'
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
__A2A_FILE_WSS_BRIDGE_PY__
cat > /opt/fuze/a2a-bridge/a2a_mcp.py <<'__A2A_FILE_A2A_MCP_PY__'
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
# The delivery gateway (agent-templates/orchestration/a2a_gateway): it runs
# `claude -p --cloud <id>`, which WAKES an idle peer — the local WSS bridge can't.
GATEWAY = os.environ.get("FUZE_A2A_GATEWAY_URL", "").rstrip("/")
GATEWAY_TOKEN = os.environ.get("FUZE_A2A_GATEWAY_TOKEN", "")

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
    """Deliver a message to a peer cloud session — and WAKE it if idle.

    Goes through the A2A delivery gateway (FUZE_A2A_GATEWAY_URL), which runs
    `claude -p --cloud <id>` server-side; that wakes an idle peer, which the local WSS
    bridge cannot. `peer` = a name from a2a_set_peer OR a raw cse_/session_ id. `reply_to`
    defaults to this session's id so the peer can reply back to you.
    """
    if not GATEWAY:
        return json.dumps({"ok": False, "error": "FUZE_A2A_GATEWAY_URL unset — no delivery gateway"})
    to = _resolve(peer) or peer  # the gateway also resolves names it has in its registry
    if not reply_to:
        reply_to = SESSION_ID
    body = {"to": to, "text": f"[A2A from {reply_to}] {text}"}
    hdrs = {"Content-Type": "application/json"}
    if GATEWAY_TOKEN:
        hdrs["Authorization"] = f"Bearer {GATEWAY_TOKEN}"
    try:
        req = urllib.request.Request(GATEWAY + "/send", data=json.dumps(body).encode(),
                                     method="POST", headers=hdrs)
        with urllib.request.urlopen(req, timeout=90) as r:  # noqa: S310 — our gateway
            return r.read().decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001
        return json.dumps({"ok": False, "error": str(e), "gateway": GATEWAY})


if __name__ == "__main__":
    mcp.run()  # stdio transport
__A2A_FILE_A2A_MCP_PY__
cat > /opt/fuze/a2a-bridge/a2a_mcp_launch.sh <<'__A2A_FILE_A2A_MCP_LAUNCH_SH__'
#!/bin/bash
# Guarded launcher for the A2A outbound MCP server (declared in the repo's .mcp.json).
#
# .mcp.json is read by EVERY FuzeInfra session (local + cloud). The MCP server is only
# meaningful in an opted-in cloud session, so this guard exits quietly otherwise. It
# logs WHY it exits to $STATE/mcp_launch.log so a "CONNECTION_CLOSED" in the client is
# diagnosable (that error just means this process exited before speaking MCP).
#
# Earlier CONNECTION_CLOSED had two candidate causes, both handled now:
#   1) .mcp.json path not expanded -> fixed in .mcp.json (runtime shell expansion +
#      $PWD fallback), so this script is actually found and run.
#   2) `mcp` not importable (wrong python / setup-script timing) -> self-heal install.
set -u
STATE="${A2A_BRIDGE_STATE:-${TMPDIR:-/tmp}/a2a-bridge}"
LOG="$STATE/mcp_launch.log"
mkdir -p "$STATE" 2>/dev/null || true
log() { echo "[a2a_mcp_launch $(date -u +%H:%M:%S)] $*" >>"$LOG" 2>/dev/null; echo "a2a_mcp: $*" >&2; }

[ "${FUZE_A2A_BRIDGE:-}" = "1" ] || { log "not opted in (FUZE_A2A_BRIDGE!=1) — exit 0"; exit 0; }
command -v python3 >/dev/null 2>&1 || { log "python3 not found — exit 0"; exit 0; }

# The precise import a2a_mcp.py needs (mcp 2.0 REMOVED mcp.server.fastmcp — hence <2).
if ! python3 -c 'import mcp.server.fastmcp' 2>/dev/null; then
  log "mcp.server.fastmcp not importable — installing 'mcp>=1.9,<2'"
  # The last retry adds --ignore-installed: mcp pulls a newer PyJWT than the
  # distro-managed one, which pip cannot uninstall ("RECORD file not found ...
  # installed by debian") — so a plain install fails. --ignore-installed layers
  # pip's own copy on top instead of trying to remove the debian package.
  pip install --quiet --no-input 'mcp>=1.9,<2' >>"$LOG" 2>&1 \
    || pip install --quiet --no-input --break-system-packages 'mcp>=1.9,<2' >>"$LOG" 2>&1 \
    || pip install --quiet --no-input --break-system-packages --ignore-installed 'mcp>=1.9,<2' >>"$LOG" 2>&1 \
    || true
fi
python3 -c 'import mcp.server.fastmcp' 2>/dev/null || { log "mcp still unimportable — exit 0"; exit 0; }

log "starting a2a_mcp.py"
exec python3 "$(cd "$(dirname "$0")" && pwd)/a2a_mcp.py"
__A2A_FILE_A2A_MCP_LAUNCH_SH__
chmod 0755 /opt/fuze/a2a-bridge/*.sh 2>/dev/null || true
# Write/merge the user-level SessionStart hook (applies to ALL sessions in this env).
CLAUDE_HOME="${HOME:-/root}"
install -d -m 0755 "$CLAUDE_HOME/.claude" || true
python3 - "$CLAUDE_HOME/.claude/settings.json" "/opt/fuze/a2a-bridge/start.sh" <<'__A2A_HOOK_MERGE__' || true
import json, os, sys
settings_path, start_sh = sys.argv[1], sys.argv[2]
cmd = "bash " + start_sh
try:
    with open(settings_path, encoding="utf-8") as f:
        data = json.load(f)
except (OSError, ValueError):
    data = {}
if not isinstance(data, dict):
    data = {}
hooks = data.setdefault("hooks", {})
if not isinstance(hooks, dict):
    hooks = data["hooks"] = {}
session_start = hooks.setdefault("SessionStart", [])
if not isinstance(session_start, list):
    session_start = hooks["SessionStart"] = []
already = any(
    (h or {}).get("command", "").strip() == cmd
    for entry in session_start
    for h in (entry.get("hooks") or [])
)
if not already:
    session_start.append({
        "matcher": "startup|resume",
        "hooks": [{"type": "command", "command": cmd}],
    })
    os.makedirs(os.path.dirname(settings_path) or ".", exist_ok=True)
    with open(settings_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    print("[setup] a2a-bridge: user-level SessionStart hook installed")
else:
    print("[setup] a2a-bridge: user-level SessionStart hook already present")
__A2A_HOOK_MERGE__

# Leave a record in the session log of what actually landed.
echo "[setup] installed:"
for b in gh kubeconform helm pytest yamllint check-jsonschema prettier; do
  printf "  %-12s %s\n" "$b" "$(command -v "$b" 2>/dev/null || echo MISSING)"
done

# Always succeed: a failed optional install must not block the session.
exit 0
