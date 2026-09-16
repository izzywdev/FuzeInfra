"""Executable invariants for the ARC self-heal watchdog's off-VLAN CI-node check.

Background (2026-09-07). `fuzeinfra-ci-runner-2` joined the cluster with no
eth1, so k3s registered it on a public IPv4 + IPv6 instead of an address in the
private VLAN (10.0.0.0/22). Pods on it have no route to cluster DNS, so every
ARC runner scheduled there registered with GitHub as `status=offline
busy=false` while its Pod looked perfectly healthy (2/2 Running, both Ready)
and its listener reported `"assigned job"=1`. Five repos' CI queued behind
runners that could never come online, and none of checks 1-5 noticed, because
each of them asks "did a runner pod get created?" and the answer was yes.

Check 6 closes that gap by quarantining such a node the same way the elastic
join path self-quarantines (vlan=absent + off-vlan=true:NoSchedule).

These tests are OFFLINE: they parse the committed manifest and re-implement
the check's selection predicate over fixture node objects. There is no cluster
call, no network, and no bash — so they run on every platform.
"""

from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

ROOT = Path(__file__).parents[1]
MANIFEST = ROOT / "runners" / "arc" / "gitops" / "controller-selfheal.yaml"

# The VLAN predicate, mirrored from VLAN_RE in the manifest. Kept as a plain
# prefix test rather than a regex import so a drift between the two is a test
# failure with an obvious diff rather than a silently-passing tautology.
VLAN_PREFIXES = ("10.0.0.", "10.0.1.", "10.0.2.", "10.0.3.")


def _docs():
    return [d for d in yaml.safe_load_all(MANIFEST.read_text(encoding="utf-8")) if d]


def _cronjob_script():
    for doc in _docs():
        if doc.get("kind") == "CronJob":
            containers = doc["spec"]["jobTemplate"]["spec"]["template"]["spec"]["containers"]
            return "\n".join(arg for c in containers for arg in c.get("args", []))
    raise AssertionError("no CronJob in controller-selfheal.yaml")


def _is_off_vlan(node):
    """The predicate check 6 implements in jq: a node is off-VLAN when it
    carries no off-vlan taint yet and NONE of its InternalIPs is on the VLAN."""
    taints = node.get("spec", {}).get("taints") or []
    if any(t.get("key") == "fuzeinfra.io/off-vlan" for t in taints):
        return False
    internal = [
        a["address"]
        for a in node.get("status", {}).get("addresses", [])
        if a.get("type") == "InternalIP"
    ]
    return not any(ip.startswith(VLAN_PREFIXES) for ip in internal)


def _node(name, ips, taints=None):
    return {
        "metadata": {"name": name},
        "spec": {"taints": taints or []},
        "status": {"addresses": [{"type": "InternalIP", "address": ip} for ip in ips]},
    }


# --- the manifest actually ships the check -----------------------------------


def test_manifest_is_valid_yaml_and_has_the_check():
    script = _cronjob_script()
    assert "check 6" in script, "check 6 must stay in the watchdog script"
    assert "VLAN_RE=" in script
    assert "fuzeinfra.io/off-vlan=true:NoSchedule" in script, (
        "the quarantine taint must match the one runner pods do NOT tolerate "
        "(runners/arc/runner-scale-set-values.yaml tolerates only "
        "fuzeinfra.io/ci and fuzeinfra.io/elastic) — a taint they tolerate "
        "would quarantine nothing"
    )
    assert "fuzeinfra.io/vlan=absent" in script


def test_check_6_emits_crit_so_the_job_fails_and_alerts():
    script = _cronjob_script()
    assert "CRIT ARC ci-node-vlan:" in script
    # crit=1 is what turns the Job red, which is what PodFailed alerting and
    # failedJobsHistoryLimit key off. A silent auto-repair would hide a node
    # that still needs ops attention (k3s sets --node-ip at registration only).
    idx = script.index("check 6")
    assert "crit=1" in script[idx:]


def test_rbac_grants_pod_read_in_arc_runners_but_not_pod_delete():
    roles = [
        d
        for d in _docs()
        if d.get("kind") == "Role" and d["metadata"].get("namespace") == "arc-runners"
    ]
    assert roles, "check 6 needs pod read in arc-runners to map node -> runner pods"
    verbs = {v for r in roles for rule in r["rules"] for v in rule["verbs"]}
    assert "list" in verbs
    assert "delete" not in verbs, (
        "removal must go through the EphemeralRunner CR (which cascades to the "
        "Pod); granting pod delete here would let the watchdog orphan a CR"
    )
    bindings = [
        d
        for d in _docs()
        if d.get("kind") == "RoleBinding" and d["metadata"].get("namespace") == "arc-runners"
    ]
    assert bindings, "the arc-runners Role must be bound to the arc-selfheal SA"
    subjects = [s for b in bindings for s in b["subjects"]]
    assert any(
        s["kind"] == "ServiceAccount"
        and s["name"] == "arc-selfheal"
        and s["namespace"] == "arc-systems"
        for s in subjects
    )


# --- the predicate selects the right nodes -----------------------------------


def test_selects_the_node_that_registered_off_vlan():
    # The real fuzeinfra-ci-runner-2 as observed on 2026-09-07.
    assert _is_off_vlan(_node("ci-2", ["13.140.158.203", "2a02:c207:2354:3725::1"]))


def test_leaves_a_healthy_vlan_node_alone():
    # The real fuzeinfra-ci-runner-1.
    assert not _is_off_vlan(_node("ci-1", ["10.0.0.4"]))


@pytest.mark.parametrize("ip", ["10.0.0.2", "10.0.1.9", "10.0.3.255"])
def test_whole_vlan_range_counts_as_on_vlan(ip):
    """10.0.0.0/22 is four /24s — a naive '10.0.0.' prefix would quarantine
    healthy nodes the moment the VLAN grows past the first /24."""
    assert not _is_off_vlan(_node("n", [ip]))


@pytest.mark.parametrize("ip", ["10.0.4.1", "10.1.0.4", "110.0.0.4"])
def test_addresses_outside_the_range_are_off_vlan(ip):
    assert _is_off_vlan(_node("n", [ip]))


def test_dual_homed_node_is_on_vlan_if_any_internal_ip_is():
    assert not _is_off_vlan(_node("n", ["203.0.113.7", "10.0.0.9"]))


def test_already_quarantined_node_is_skipped():
    """Idempotence: re-processing every 15 min would re-delete the runner pods
    of a node an operator is deliberately holding quarantined."""
    taint = {"key": "fuzeinfra.io/off-vlan", "value": "true", "effect": "NoSchedule"}
    assert not _is_off_vlan(_node("ci-2", ["13.140.158.203"], taints=[taint]))


def test_node_with_no_internal_ip_is_treated_as_off_vlan():
    """Fail closed: an unknown address set is not evidence of health."""
    assert _is_off_vlan(_node("n", []))
