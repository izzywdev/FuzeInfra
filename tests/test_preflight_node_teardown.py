"""The gate that would have prevented the 2026-09-07 Prometheus data loss.

The centrepiece is `test_reproduces_the_2026_09_07_prometheus_loss`, which feeds the
checker the EXACT Longhorn state that existed minutes before `vmi3383846` was wiped
and asserts it refuses. The old check ("does a replica exist off this node?") passed
on that same input, and the TSDB was destroyed.

Everything here is offline: fixtures are dicts, no cluster, no kubectl.
"""

from __future__ import annotations

import datetime as _dt
import importlib.util
import pathlib

import pytest

_MOD = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "preflight_node_teardown.py"
_spec = importlib.util.spec_from_file_location("preflight_node_teardown", _MOD)
pf = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pf)

NOW = _dt.datetime(2026, 9, 7, 8, 20, 0, tzinfo=_dt.timezone.utc)


def rep(volume, node, *, running=True, failed_at=None):
    return {
        "metadata": {"name": f"{volume}-r-{node}"},
        "spec": {"volumeName": volume, "nodeID": node, "failedAt": failed_at or ""},
        "status": {"currentState": "running" if running else "stopped"},
    }


def vol(name, *, replicas=3, robustness="healthy", state="attached"):
    return {
        "metadata": {"name": name},
        "spec": {"numberOfReplicas": replicas},
        "status": {"robustness": robustness, "state": state},
    }


def wrap(items):
    return {"items": items}


# --------------------------------------------------------------------------
# The regression this file exists for
# --------------------------------------------------------------------------

def test_reproduces_the_2026_09_07_prometheus_loss():
    """Exact state before wiping vmi3383846. The old check passed here."""
    volumes = wrap([vol("pvc-prometheus", replicas=3, robustness="degraded")])
    replicas = wrap([
        # killed 25 minutes earlier when vmi3396106 was rebuilt as fuze-core-2
        rep("pvc-prometheus", "vmi3396106", running=False,
            failed_at="2026-09-07T07:56:13Z"),
        # the copy on the node about to be wiped -- the only intact one
        rep("pvc-prometheus", "vmi3383846"),
        # a rebuild started minutes ago: exists, but NOT yet a usable copy
        rep("pvc-prometheus", "fuze-core-2", running=False),
    ])

    ok, blockers, summary = pf.evaluate("vmi3383846", volumes, replicas, now=NOW)

    assert ok is False, "the gate must refuse the teardown that destroyed the TSDB"
    assert summary["volumes_at_risk"] == 1
    joined = " ".join(blockers)
    assert "pvc-prometheus" in joined
    # both independent reasons should be reported, not just the first
    assert "fewer than 2 intact copies" in joined
    assert "still in flight" in joined


def test_the_old_check_would_have_passed_the_same_input():
    """Pins WHY this gate exists: 'a replica exists elsewhere' is not safety.

    If this ever fails, the old rule has become equivalent to the new one and the
    regression fixture above has lost its meaning.
    """
    replicas = [
        rep("pvc-prometheus", "vmi3396106", running=False,
            failed_at="2026-09-07T07:56:13Z"),
        rep("pvc-prometheus", "vmi3383846"),
        rep("pvc-prometheus", "fuze-core-2", running=False),
    ]
    old_check_passes = any(r["spec"]["nodeID"] != "vmi3383846" for r in replicas)
    assert old_check_passes is True


# --------------------------------------------------------------------------
# Core behaviour
# --------------------------------------------------------------------------

def test_allows_teardown_with_two_intact_copies_elsewhere():
    volumes = wrap([vol("pvc-ok")])
    replicas = wrap([
        rep("pvc-ok", "fuze-core-1"),
        rep("pvc-ok", "fuze-core-2"),
        rep("pvc-ok", "fuze-core-3"),
    ])
    ok, blockers, summary = pf.evaluate("fuze-core-1", volumes, replicas, now=NOW)
    assert ok is True, blockers
    assert summary["volumes_at_risk"] == 0


def test_refuses_single_replica_volume():
    """numberOfReplicas=1 is guaranteed loss; Prometheus sat here for days."""
    volumes = wrap([vol("pvc-single", replicas=1)])
    replicas = wrap([rep("pvc-single", "fuze-core-1")])
    ok, blockers, _ = pf.evaluate("fuze-core-1", volumes, replicas, now=NOW)
    assert ok is False
    assert "pvc-single" in " ".join(blockers)


def test_failed_replica_elsewhere_does_not_count_as_a_copy():
    """A stopped/failed replica keeps its nodeID and looks real. It is not a copy."""
    volumes = wrap([vol("pvc-x")])
    replicas = wrap([
        rep("pvc-x", "fuze-core-1"),
        rep("pvc-x", "fuze-core-2", running=False, failed_at="2026-01-01T00:00:00Z"),
        rep("pvc-x", "fuze-core-3", running=False, failed_at="2026-01-01T00:00:00Z"),
    ])
    ok, blockers, _ = pf.evaluate("fuze-core-1", volumes, replicas, now=NOW)
    assert ok is False
    assert "0 intact replica(s) would survive" in " ".join(blockers)


def test_old_failures_do_not_trip_the_cooldown():
    """Long-past failures are history, not an in-flight rebuild."""
    volumes = wrap([vol("pvc-y")])
    replicas = wrap([
        rep("pvc-y", "fuze-core-1"),
        rep("pvc-y", "fuze-core-2"),
        rep("pvc-y", "fuze-core-3"),
        rep("pvc-y", "gone-node", running=False, failed_at="2026-01-01T00:00:00Z"),
    ])
    ok, blockers, _ = pf.evaluate("fuze-core-1", volumes, replicas, now=NOW)
    assert ok is True, blockers


def test_already_dead_target_node_is_not_a_special_case():
    """Wiping an already-dead node must be allowed if the survivors are intact."""
    volumes = wrap([vol("pvc-z")])
    replicas = wrap([
        rep("pvc-z", "dead-node", running=False, failed_at="2026-01-01T00:00:00Z"),
        rep("pvc-z", "fuze-core-2"),
        rep("pvc-z", "fuze-core-3"),
    ])
    ok, blockers, _ = pf.evaluate("dead-node", volumes, replicas, now=NOW)
    assert ok is True, blockers


# --------------------------------------------------------------------------
# Fail-closed
# --------------------------------------------------------------------------

@pytest.mark.parametrize("volumes,replicas,why", [
    ({}, {"items": []}, "unreadable volumes"),
    ({"items": []}, {"items": []}, "zero volumes parsed"),
    ({"items": [vol("v")]}, {}, "unreadable replicas"),
])
def test_fails_closed_on_unusable_input(volumes, replicas, why):
    ok, blockers, _ = pf.evaluate("n", volumes, replicas, now=NOW)
    assert ok is False, why
    assert blockers


def test_fails_closed_on_unparseable_timestamp():
    volumes = wrap([vol("v")])
    replicas = wrap([rep("v", "a", running=False, failed_at="not-a-date")])
    ok, blockers, _ = pf.evaluate("b", volumes, replicas, now=NOW)
    assert ok is False
    assert "unparseable" in " ".join(blockers)


def test_volume_with_no_replica_records_is_reported():
    volumes = wrap([vol("orphan")])
    ok, blockers, _ = pf.evaluate("any", volumes, wrap([]), now=NOW)
    assert ok is False
    assert "orphan" in " ".join(blockers)


def test_detached_volume_replicas_count_even_though_not_running():
    """A DETACHED volume has no running replicas by definition; its data is fine.

    Requiring currentState == running unconditionally reported every detached volume
    as having zero copies. Against live cluster state that flagged 7 healthy volumes
    and would have blocked every teardown permanently -- a gate that always refuses
    gets switched off, which is worse than no gate.
    """
    volumes = wrap([vol("pvc-detached", state="detached", robustness="unknown")])
    replicas = wrap([
        rep("pvc-detached", "fuze-core-1", running=False),
        rep("pvc-detached", "fuze-core-2", running=False),
        rep("pvc-detached", "fuze-core-3", running=False),
    ])
    ok, blockers, _ = pf.evaluate("fuze-core-1", volumes, replicas, now=NOW)
    assert ok is True, blockers


def test_detached_volume_with_failed_replicas_still_blocks():
    """failedAt still disqualifies a replica even when the volume is detached."""
    volumes = wrap([vol("pvc-detached-bad", state="detached", robustness="unknown")])
    replicas = wrap([
        rep("pvc-detached-bad", "fuze-core-1", running=False),
        rep("pvc-detached-bad", "gone-a", running=False, failed_at="2026-01-01T00:00:00Z"),
        rep("pvc-detached-bad", "gone-b", running=False, failed_at="2026-01-01T00:00:00Z"),
    ])
    ok, blockers, _ = pf.evaluate("fuze-core-1", volumes, replicas, now=NOW)
    assert ok is False
    assert "0 intact replica(s) would survive" in " ".join(blockers)
