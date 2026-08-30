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
