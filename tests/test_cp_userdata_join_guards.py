"""Guards the self-joining control-plane cloud-init's two recovery invariants.

WHY THIS FILE EXISTS
--------------------
`cluster-autoscaler/contabo-externalgrpc/deploy/cp-userdata-eth1-join.template`
is the template `ca-reinstall-controlplane.yml` reinstalls a control plane FROM,
and the one the dark-node escalation ladder in `scripts-tools/deployment_watchdog.py`
always uses. Its whole recovery design rests on one property: the join runs from a
systemd oneshot gated on `ConditionPathExists=!/etc/fuzeinfra-cp-joined`, so it
re-runs on EVERY boot until it has actually succeeded.

On 2026-09-15 `fuze-core-3` was reinstalled and never came back. Both halves of
the failure were in this template:

  1. The `K3S_SERVER_URL` secret held the node's OWN public IP, so k3s asked
     itself for `/cacerts` before it was serving and crash-looped 341 times with
     "failed to validate token ... connection refused". Nothing named the cause.
  2. The install had already written `/etc/systemd/system/k3s.service` before
     failing, so the NEXT boot took the "k3s server already installed; leaving it
     alone" branch and wrote the joined sentinel -- permanently disabling the
     retry-on-boot mechanism, while the previous boot's log still claimed "the
     unit retries on next boot".

A node that can never retry and never explains why is the worst possible
failure mode for an automated remediation ladder. These tests make both
regressions a red build.

Offline: no cluster, no Contabo API, no secrets.
"""

from __future__ import annotations

import pathlib
import re
import shutil
import subprocess

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
TEMPLATE = (
    REPO
    / "cluster-autoscaler"
    / "contabo-externalgrpc"
    / "deploy"
    / "cp-userdata-eth1-join.template"
)


@pytest.fixture(scope="module")
def template_text() -> str:
    assert TEMPLATE.is_file(), f"missing template: {TEMPLATE}"
    return TEMPLATE.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def join_script(template_text: str) -> str:
    """Extract the embedded /usr/local/sbin/fuzeinfra-cp-join.sh body.

    The script is a YAML block scalar indented 6 spaces under `content: |`.
    """
    marker = "- path: /usr/local/sbin/fuzeinfra-cp-join.sh"
    start = template_text.index(marker)
    content_at = template_text.index("content: |", start)
    body_start = template_text.index("\n", content_at) + 1

    lines: list[str] = []
    for line in template_text[body_start:].splitlines():
        if line.strip() and not line.startswith("      "):
            break
        lines.append(line[6:] if len(line) >= 6 else line)
    script = "\n".join(lines)
    assert "#!/bin/sh" in script.splitlines()[0], "did not extract the join script"
    return script


def test_join_script_is_valid_posix_shell(join_script: str, tmp_path: pathlib.Path) -> None:
    """The script ships as-is to a fresh host; a syntax error is unrecoverable there.

    Go-template actions are placeholders, not shell -- substitute them with inert
    literals before the parse so this checks OUR syntax, not the renderer's.
    """
    sh = shutil.which("sh") or shutil.which("bash")
    if sh is None:  # pragma: no cover - CI always has a shell
        pytest.skip("no POSIX shell available")

    rendered = re.sub(r"\{\{\.[A-Za-z0-9_]+\}\}", "PLACEHOLDER", join_script)
    path = tmp_path / "join.sh"
    path.write_text(rendered, encoding="utf-8")

    proc = subprocess.run([sh, "-n", str(path)], capture_output=True, text=True)
    assert proc.returncode == 0, f"join script is not valid POSIX sh:\n{proc.stderr}"


def test_already_installed_branch_requires_k3s_to_be_active(join_script: str) -> None:
    """Regression: a crash-looping k3s must NOT be mistaken for a completed join.

    Writing the sentinel is what permanently disables retry-on-boot, so it may
    only happen behind a liveness check -- never on the mere existence of the
    unit file.
    """
    branch = join_script[join_script.index("/etc/systemd/system/k3s.service") :]
    # Everything up to the end of that if-block.
    branch = branch[: branch.index("\nip4_of()")]

    assert "systemctl is-active" in branch, (
        "the 'already installed' branch must verify k3s is ACTIVE before it "
        "writes the joined sentinel; the unit file alone is left behind by a "
        "FAILED install (fuze-core-3, 2026-09-15)"
    )

    # The sentinel write inside this branch must be guarded by the liveness
    # check, i.e. it must come after it.
    touch_at = branch.index('touch "$SENTINEL"')
    active_at = branch.index("systemctl is-active")
    assert active_at < touch_at, (
        "the sentinel is written before/without the is-active check -- a failed "
        "install would permanently disable the retry-on-boot mechanism"
    )


def test_refuses_when_server_url_points_at_this_node(join_script: str) -> None:
    """Regression: bootstrapping from your own address can never succeed.

    k3s fetches $K3S_URL/cacerts before it serves, so a self-referential
    K3S_SERVER_URL yields an endless silent crash-loop. The template must name
    that specific cause rather than let it look like a dead host.
    """
    assert "$K3S_URL" in join_script
    # The guard is a loop over this node's own addresses wrapping a case on
    # $K3S_URL; match the whole block so the addresses it iterates are in scope.
    guard = re.search(
        r"for _self in .*?case \"\$K3S_URL\" in.*?esac.*?done",
        join_script,
        flags=re.DOTALL,
    )
    assert guard is not None, (
        "no guard rejecting a self-referential K3S_URL; fuze-core-3 crash-looped "
        "341 times on 2026-09-15 because K3S_SERVER_URL held its own public IP"
    )
    guard_text = guard.group(0)
    for var in ("$PRIV", "$PUB"):
        assert var in guard_text or var.strip("$") in guard_text, (
            f"the self-URL guard must consider {var}: the secret held the node's "
            "PUBLIC address while the join advertises its PRIVATE one"
        )
    assert "exit 1" in guard_text, "the self-URL guard must refuse, not warn"


def test_sentinel_success_path_is_still_reachable(join_script: str) -> None:
    """The fix must not strand the happy path: a real join still writes the sentinel."""
    tail = join_script[join_script.index("get.k3s.io") :]
    assert 'touch "$SENTINEL"' in tail, (
        "a successful install must still write the sentinel, or every boot "
        "would reinstall k3s"
    )
