#!/bin/bash
# Guarded launcher for the A2A outbound MCP server (declared in the repo's .mcp.json).
#
# This .mcp.json is read by EVERY FuzeInfra session, local and cloud. The bridge is
# only meaningful in an opted-in cloud session, so this guard exits quietly otherwise
# rather than crash-looping a Python import. It runs the server only when:
#   - the environment opted in (FUZE_A2A_BRIDGE=1, set in the Fuze/DevOps cloud env), and
#   - python3 + the `mcp` package are present (installed by the env setup script).
# Otherwise it exits 0 and Claude simply shows the server as unavailable.
#
# Self-heal: the setup script installs `mcp` in a backgrounded, || true pip line, so a
# single flaky/slow package there can silently leave `mcp` uninstalled — which surfaces
# only as the MCP server closing at spawn (CONNECTION_CLOSED). Rather than degrade
# silently, try one best-effort install before giving up. PyPI is reachable in an
# opted-in cloud session (allow_package_managers), so this recovers the tools without a
# rebuild; it runs only when `mcp` is actually missing, so the steady state pays nothing.
set -u

[ "${FUZE_A2A_BRIDGE:-}" = "1" ] || { echo "a2a_mcp: not opted in (FUZE_A2A_BRIDGE!=1)" >&2; exit 0; }
command -v python3 >/dev/null 2>&1 || { echo "a2a_mcp: python3 not found" >&2; exit 0; }

if ! python3 -c 'import mcp' 2>/dev/null; then
  echo "a2a_mcp: 'mcp' missing — attempting one-shot install" >&2
  # The last retry adds --ignore-installed: mcp pulls a newer PyJWT than the
  # distro-managed one, which pip cannot uninstall ("RECORD file not found ...
  # installed by debian") — so a plain install fails. --ignore-installed layers
  # pip's own copy on top instead of trying to remove the debian package.
  pip install --quiet --no-input 'mcp>=1.9,<2' >/dev/null 2>&1 \
    || pip install --quiet --no-input --break-system-packages 'mcp>=1.9,<2' >/dev/null 2>&1 \
    || pip install --quiet --no-input --break-system-packages --ignore-installed 'mcp>=1.9,<2' >/dev/null 2>&1 \
    || true
fi
python3 -c 'import mcp' 2>/dev/null \
  || { echo "a2a_mcp: 'mcp' still unavailable after install attempt; MCP tools disabled" >&2; exit 0; }

exec python3 "$(cd "$(dirname "$0")" && pwd)/a2a_mcp.py"
