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
