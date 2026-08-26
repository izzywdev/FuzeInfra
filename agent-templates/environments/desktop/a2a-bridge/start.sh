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

setsid nohup python3 "$HERE/wss_bridge.py" >"$STATE/bridge.log" 2>&1 &
echo $! >"$STATE/bridge.pid"

echo "======================================================================"
echo " A2A bridge starting for session: ${CLAUDE_CODE_REMOTE_SESSION_ID:-unknown}"
echo " Relay: ${FUZE_A2A_RELAY_URL:-<FUZE_A2A_RELAY_URL unset>}"
echo " To talk to THIS session, a peer runs:  a2a_set_peer <name> ${CLAUDE_CODE_REMOTE_SESSION_ID:-<id>}"
echo " Then check connection with the a2a_whoami MCP tool. Logs: $STATE/bridge.log"
echo "======================================================================"
exit 0
