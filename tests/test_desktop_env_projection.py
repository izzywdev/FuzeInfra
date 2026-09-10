"""Offline guard for the desktop cloud-environment projection.

Two things this locks down:

1. The committed Setup scripts (`*.setup.sh`) match what `render.py` produces — the
   `render.py --check` gate — so a change to `render.py` or a `cloud-*.json` that is not
   re-rendered fails CI instead of silently shipping a stale paste-into-the-dialog script.

2. The A2A cloud<->cloud bridge launcher is installed at the ENVIRONMENT (user) level, not
   only by FuzeInfra's repo-level SessionStart hook. Without this, the bridge daemon starts
   only when FuzeInfra is the checked-out repo; sessions on any other repo never run it. The
   Setup script must therefore (a) drop a stable, repo-independent copy of the bridge scripts
   at /opt/fuze/a2a-bridge and (b) write a user-level SessionStart hook (matcher startup|resume)
   that invokes that copy. The embedded copies must byte-for-byte match the source scripts, and
   the hook-merge must be additive + idempotent so it never clobbers an existing settings.json
   nor double-registers.

Offline: no services, no network. Runs in the infrastructure-tests offline unit block.
"""
import importlib.util
import json
import os
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
DESKTOP = os.path.normpath(
    os.path.join(HERE, "..", "agent-templates", "environments", "desktop")
)
RENDER_PY = os.path.join(DESKTOP, "render.py")
BRIDGE_DIR = os.path.join(DESKTOP, "a2a-bridge")
INSTALL_DIR = "/opt/fuze/a2a-bridge"
BRIDGE_FILES = ("start.sh", "wss_bridge.py", "a2a_mcp.py", "a2a_mcp_launch.sh")
SETUP_SCRIPTS = ("fuze.setup.sh", "devops.setup.sh")


def _load_render():
    spec = importlib.util.spec_from_file_location("desktop_render", RENDER_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_render_check_matches_committed_projection():
    """`render.py --check` is green — committed Setup scripts are not stale."""
    result = subprocess.run(
        [sys.executable, RENDER_PY, "--check"],
        capture_output=True, text=True, cwd=DESKTOP,
    )
    assert result.returncode == 0, (
        "committed desktop projection is stale — run `python render.py`.\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )


@pytest.mark.parametrize("script", SETUP_SCRIPTS)
def test_setup_script_installs_env_level_bridge_launcher(script):
    """Each Setup script drops the stable bridge copy and writes a user-level hook."""
    body = open(os.path.join(DESKTOP, script), encoding="utf-8").read()
    assert f"install -d -m 0755 {INSTALL_DIR}" in body
    for name in BRIDGE_FILES:
        assert f"cat > {INSTALL_DIR}/{name} <<" in body, f"{script} missing embed of {name}"
    # A user-level SessionStart hook, invoking the stable start.sh copy, merged into
    # ~/.claude/settings.json (not the repo-level .claude/settings.json).
    assert 'CLAUDE_HOME="${HOME:-/root}"' in body
    assert f'"$CLAUDE_HOME/.claude/settings.json" "{INSTALL_DIR}/start.sh"' in body


@pytest.mark.parametrize("script", SETUP_SCRIPTS)
@pytest.mark.parametrize("name", BRIDGE_FILES)
def test_embedded_bridge_scripts_match_source(script, name):
    """The inlined heredoc body is byte-for-byte the source script (no embed drift)."""
    with open(os.path.join(BRIDGE_DIR, name), encoding="utf-8") as source_file:
        source = source_file.read().rstrip("\n")
    body = open(os.path.join(DESKTOP, script), encoding="utf-8").read()
    delim = "__A2A_FILE_" + name.upper().replace(".", "_") + "__"
    open_marker = f"cat > {INSTALL_DIR}/{name} <<'{delim}'\n"
    start = body.index(open_marker) + len(open_marker)
    end = body.index(f"\n{delim}\n", start)
    assert body[start:end] == source, f"embedded {name} in {script} differs from source"


def test_hook_merge_is_additive_and_idempotent(tmp_path):
    """The merge python adds the hook to a fresh file, preserves existing settings, and
    does not duplicate on a second run."""
    render = _load_render()
    merge_src = tmp_path / "merge.py"
    merge_src.write_text(render._A2A_HOOK_MERGE_PY, encoding="utf-8")
    settings = tmp_path / ".claude" / "settings.json"
    start_sh = f"{INSTALL_DIR}/start.sh"
    expected_cmd = f"bash {start_sh}"

    def run_merge():
        return subprocess.run(
            [sys.executable, str(merge_src), str(settings), start_sh],
            capture_output=True, text=True,
        )

    # Pre-existing settings with unrelated keys must survive the merge.
    settings.parent.mkdir(parents=True)
    settings.write_text(json.dumps({"model": "opus", "hooks": {"Stop": [{"x": 1}]}}))

    r1 = run_merge()
    assert r1.returncode == 0, r1.stderr
    data = json.loads(settings.read_text())
    assert data["model"] == "opus"                       # unrelated key preserved
    assert data["hooks"]["Stop"] == [{"x": 1}]           # unrelated hook preserved
    ss = data["hooks"]["SessionStart"]
    entries = [h for e in ss for h in (e.get("hooks") or [])]
    assert [h for h in entries if h["command"] == expected_cmd], "hook not installed"
    assert any(e.get("matcher") == "startup|resume" for e in ss)

    # Second run is idempotent — no duplicate registration.
    r2 = run_merge()
    assert r2.returncode == 0, r2.stderr
    ss2 = json.loads(settings.read_text())["hooks"]["SessionStart"]
    cmds = [h["command"] for e in ss2 for h in (e.get("hooks") or [])]
    assert cmds.count(expected_cmd) == 1, f"hook duplicated: {cmds}"


def test_hook_merge_handles_missing_and_corrupt_settings(tmp_path):
    """A missing or non-JSON settings file must not crash the merge (build must exit 0)."""
    render = _load_render()
    merge_src = tmp_path / "merge.py"
    merge_src.write_text(render._A2A_HOOK_MERGE_PY, encoding="utf-8")
    start_sh = f"{INSTALL_DIR}/start.sh"

    # (a) missing file
    missing = tmp_path / "a" / "settings.json"
    r = subprocess.run([sys.executable, str(merge_src), str(missing), start_sh],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert (tmp_path / "a" / "settings.json").exists()

    # (b) corrupt file -> treated as empty, overwritten with a valid hook block
    corrupt = tmp_path / "settings.json"
    corrupt.write_text("{not json")
    r = subprocess.run([sys.executable, str(merge_src), str(corrupt), start_sh],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    data = json.loads(corrupt.read_text())
    assert data["hooks"]["SessionStart"]
