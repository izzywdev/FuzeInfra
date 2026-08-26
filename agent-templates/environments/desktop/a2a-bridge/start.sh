#!/bin/bash
# A2A bridge launcher — invoked by the repo's SessionStart hook (.claude/settings.json).
#
# Cloud-only + opt-in: no-ops unless this is a cloud session (CLAUDE_CODE_REMOTE=true)
# AND the environment opted in (FUZE_A2A_BRIDGE=1, set in the DevOps env's .env). So it
# never starts a tunnel on your laptop or in an environment that didn't ask for it.
#
# It (1) starts a cloudflared quick tunnel (no token — cloud envs have no secret store),
# (2) starts server.py behind it (inbound -> session inbox socket), and (3) prints the
# public URL + session id so you can paste it into the peer session (spike: manual URL
# passing; the durable version registers with a FuzeAgent rendezvous instead).
#
# Daemons are detached so they survive the hook returning; the hook must not block
# session start, so everything is backgrounded and we exit 0 fast.
set -u

[ "${CLAUDE_CODE_REMOTE:-}" = "true" ] || { echo "[a2a-bridge] not a cloud session; skip"; exit 0; }
[ "${FUZE_A2A_BRIDGE:-}" = "1" ]      || { echo "[a2a-bridge] FUZE_A2A_BRIDGE!=1; skip"; exit 0; }

HERE="$(cd "$(dirname "$0")" && pwd)"
PORT="${A2A_BRIDGE_PORT:-8760}"
STATE="${A2A_BRIDGE_STATE:-${TMPDIR:-/tmp}/a2a-bridge}"
mkdir -p "$STATE"

# Idempotent: SessionStart fires on startup AND resume — don't double-start.
if [ -f "$STATE/server.pid" ] && kill -0 "$(cat "$STATE/server.pid" 2>/dev/null)" 2>/dev/null; then
  echo "[a2a-bridge] already running (pid $(cat "$STATE/server.pid")); url: $(cat "$STATE/public_url" 2>/dev/null)"
  exit 0
fi

# 1) Quick tunnel (best-effort). cloudflared logs the https://*.trycloudflare.com URL.
PUBLIC_URL=""
if command -v cloudflared >/dev/null 2>&1; then
  ( setsid cloudflared tunnel --no-autoupdate --url "http://localhost:${PORT}" \
      >"$STATE/cloudflared.log" 2>&1 & echo $! >"$STATE/cloudflared.pid" ) || true
  # Wait up to ~30s for the URL to appear in the log.
  for _ in $(seq 1 30); do
    PUBLIC_URL="$(grep -Eo 'https://[a-z0-9-]+\.trycloudflare\.com' "$STATE/cloudflared.log" 2>/dev/null | head -1)"
    [ -n "$PUBLIC_URL" ] && break
    sleep 1
  done
else
  echo "[a2a-bridge] cloudflared not found — starting server for LOCAL testing only" >&2
fi
printf '%s' "$PUBLIC_URL" >"$STATE/public_url"

# 2) Inbound server behind the tunnel. Inherits CLAUDE_CODE_MESSAGING_SOCKET/TOKEN
#    from the hook (own-child of this session), which is what lets it inject a turn.
A2A_PUBLIC_URL="$PUBLIC_URL" A2A_BRIDGE_PORT="$PORT" \
  setsid nohup python3 "$HERE/server.py" >"$STATE/server.log" 2>&1 &
echo $! >"$STATE/server.pid"

# 3) Tell the operator (this goes into the session transcript).
echo "======================================================================"
echo " A2A bridge up for session: ${CLAUDE_CODE_REMOTE_SESSION_ID:-unknown}"
if [ -n "$PUBLIC_URL" ]; then
  echo " Public URL (paste into the peer session's a2a_send target_url):"
  echo "   $PUBLIC_URL"
else
  echo " No public URL — tunnel unavailable; local testing only:"
  echo "   curl -s localhost:${PORT}/health"
fi
echo " Logs: $STATE/{cloudflared,server}.log"
echo "======================================================================"
exit 0
