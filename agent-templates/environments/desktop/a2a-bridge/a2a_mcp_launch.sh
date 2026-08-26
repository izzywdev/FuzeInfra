#!/bin/bash
# Guarded launcher for the A2A outbound MCP server (declared in the repo's .mcp.json).
#
# This .mcp.json is read by EVERY FuzeInfra session, local and cloud. The bridge is
# only meaningful in an opted-in cloud session, so this guard exits quietly otherwise
# rather than crash-looping a Python import. It runs the server only when:
#   - the environment opted in (FUZE_A2A_BRIDGE=1, set in the DevOps cloud env), and
#   - python3 + the `mcp` package are present (installed by the DevOps setup script).
# Otherwise it exits 0 and Claude simply shows the server as unavailable.
set -u

[ "${FUZE_A2A_BRIDGE:-}" = "1" ] || { echo "a2a_mcp: not opted in (FUZE_A2A_BRIDGE!=1)" >&2; exit 0; }
command -v python3 >/dev/null 2>&1 || { echo "a2a_mcp: python3 not found" >&2; exit 0; }
python3 -c 'import mcp' 2>/dev/null || { echo "a2a_mcp: 'mcp' package not installed" >&2; exit 0; }

exec python3 "$(cd "$(dirname "$0")" && pwd)/a2a_mcp.py"
