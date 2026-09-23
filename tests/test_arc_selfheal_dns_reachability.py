"""Executable invariants for the ARC self-heal watchdog's check 7 (CI-node
pod-network reachability to cluster DNS).

Background (2026-09-23). The two CI nodes (fuzeinfra-ci-runner-1/-2) lost
flannel-WireGuard peering to the fuze-core-* control-plane nodes, which is
where CoreDNS's (at the time single) replica ran. Every pod on the CI nodes
stayed Running/Ready -- kubelet was fine and the apiserver path was fine --
but could resolve NO name at all, because the pod-network route to CoreDNS
was gone. Check 6 (test_arc_selfheal_offvlan.py) did not catch this: both
nodes kept a normal private-VLAN InternalIP the entire time, so the
off-VLAN predicate saw nothing wrong. Checks 1-6 all ask the apiserver
questions ABOUT a node (labels, taints, addresses); none of them sends a
packet over the pod network FROM a CI node. ~300 jobs queued fleet-wide
before this check existed.

Check 7 closes that gap by scheduling a short-lived diagnostic pod PINNED
to each pool=ci node and nslookup-ing the in-cluster kube-dns Service from
inside it -- the exact path that broke. It does not auto-remediate (no
privileged host access is granted to this ServiceAccount to restart
k3s-agent); it emits CRIT naming the concrete fix instead.

These tests are OFFLINE: they parse the committed manifest and assert on
its shape/content. There is no cluster call, no network, and no bash -- so
they run on every platform. They intentionally do NOT try to execute or
simulate the shell script's runtime behaviour (that would require a live
cluster); they assert the invariants that matter for correctness on paper:
the check exists, emits CRIT with the remediation, does not silently
auto-restart anything, and the RBAC it depends on was extended alongside
it (the documented trap: a new check against stale RBAC silently returns
empty and either a false "ok" or a false CRIT, not a loud permission
error).
"""

from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

ROOT = Path(__file__).parents[1]
MANIFEST = ROOT / "runners" / "arc" / "gitops" / "controller-selfheal.yaml"


def _docs():
    return [d for d in yaml.safe_load_all(MANIFEST.read_text(encoding="utf-8")) if d]


def _cronjob_script():
    for doc in _docs():
        if doc.get("kind") == "CronJob":
            containers = doc["spec"]["jobTemplate"]["spec"]["template"]["spec"]["containers"]
            return "\n".join(arg for c in containers for arg in c.get("args", []))
    raise AssertionError("no CronJob in controller-selfheal.yaml")


# --- the manifest actually ships the check -----------------------------------


def test_manifest_has_check_7():
    script = _cronjob_script()
    assert "check 7" in script, "check 7 (CI-node DNS reachability) must stay in the watchdog script"


def test_check_7_probes_every_pool_ci_node_by_pinning_a_pod_to_it():
    script = _cronjob_script()
    idx = script.index("check 7")
    body = script[idx:]
    assert "fuzeinfra.io/pool=ci" in body, "must enumerate the same CI node set the outage hit"
    assert "nodeSelector" in body and "kubernetes.io/hostname" in body, (
        "the probe pod must be PINNED to the node under test via nodeSelector -- "
        "an unpinned pod could land anywhere and prove nothing about that node"
    )
    assert "nslookup" in body and "kubernetes.default.svc.cluster.local" in body, (
        "must actually resolve a name from inside the pinned pod, exercising the "
        "pod -> CNI -> overlay -> CoreDNS path that broke on 2026-09-23"
    )


def test_check_7_emits_crit_with_the_concrete_remediation_and_does_not_auto_restart():
    script = _cronjob_script()
    idx = script.index("check 7")
    body = script[idx:]
    assert "CRIT ARC ci-node-dns:" in body
    assert "crit=1" in body
    # The live fix was restarting k3s-agent so flanneld rebuilds its WireGuard
    # peers. This check must NAME that remediation, not attempt it -- it has no
    # privileged host access, so a wrong guess here has no rollback.
    assert "k3s-agent" in body
    # The remediation text may (and does) mention the command as documentation
    # in comments and in the CRIT echo string -- that's fine. What must NOT
    # exist is a bare, executable invocation of it as its own script line (the
    # shape check 1's `kubectl taint node ...` or check 3's `kubectl delete pod
    # ...` take): this check has no privileged host access to run it anyway,
    # but a bare invocation line would also be the first thing a future edit
    # might "helpfully" wire up without noticing it can't work.
    assert not any(
        line.strip() == "systemctl restart k3s-agent" for line in body.splitlines()
    ), "systemctl restart k3s-agent must only appear as documentation/CRIT text, never as an executable script line"


def test_check_7_cleans_up_its_own_probe_pods():
    script = _cronjob_script()
    idx = script.index("check 7")
    body = script[idx:]
    assert "kubectl delete pod" in body, (
        "each diagnostic pod must be deleted after use -- a 15-min cron leaking "
        "one pod per CI node per run would slowly fill arc-systems"
    )


def test_probe_pod_names_are_valid_dns_labels_regardless_of_node_name_casing():
    """kubectl run's pod name must be a valid DNS-1123 label. Node names in this
    fleet are already lowercase, but the script must not assume that forever --
    it should normalize case and disallowed characters rather than pass the raw
    node name straight into a pod name."""
    script = _cronjob_script()
    idx = script.index("check 7")
    body = script[idx:]
    assert "tr 'A-Z' 'a-z'" in body or "tr '[:upper:]' '[:lower:]'" in body, (
        "must lowercase the node name before using it in a pod name"
    )


# --- RBAC was extended alongside the check, not left stale --------------------


def test_arc_systems_role_grants_pod_create_for_the_dns_probe():
    roles = [
        d
        for d in _docs()
        if d.get("kind") == "Role" and d["metadata"].get("namespace") == "arc-systems"
    ]
    assert roles, "check 7's probe pods live in arc-systems"
    verbs = {v for r in roles for rule in r["rules"] for v in rule["verbs"]}
    # The documented trap: a new check added against OLD RBAC doesn't fail loudly
    # (kubectl run against a resource you can't create errors, which this test
    # guards stays possible) -- so "create" must actually be present, not just
    # "get"/"list"/"delete" carried over from check 3.
    assert "create" in verbs, (
        "check 7 needs pods/create in arc-systems for the per-CI-node DNS probe "
        "pod -- without it kubectl run fails and the check can never run"
    )
    assert "get" in verbs and "delete" in verbs, "still needed to poll phase and clean up"


def test_manifest_is_valid_yaml_with_expected_doc_count():
    """Sanity check that this file (and this test's edits alongside it) still
    parses as exactly the documents the RBAC/CronJob tests above expect."""
    docs = _docs()
    kinds = [d["kind"] for d in docs]
    assert kinds.count("CronJob") == 1
    assert kinds.count("Role") == 2, "arc-systems Role + arc-runners Role"
