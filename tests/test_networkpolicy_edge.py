"""The edge egress lockdown must stay a lockdown.

`helm/fuzeinfra/templates/networkpolicy-edge.yaml` stops the internet-facing
pods (a2a-relay, a2a-gateway, handoff-mcp) reaching any in-cluster destination.
a2a-relay is the sharp case: it is published at relay.<domain> and in v0 carries
NO bearer, so before this policy an RCE in it reached postgres:5432,
redis:6379, mongodb:27017, neo4j:7687 and elasticsearch:9200 directly.

Every assertion here guards a way the policy can silently stop protecting
without anything going red:

  * an empty `except` list renders a valid NetworkPolicy that blocks nothing
  * a missing DNS rule breaks the pods outright (the classic deny-egress footgun)
  * dropping the opt-in label from a Deployment removes it from the policy while
    the policy itself still looks correct
  * adding Ingress to policyTypes would silently start filtering Prometheus
    scrapes and kubelet probes, which this policy deliberately does not do

Offline: renders the chart with `helm template`. No cluster, no network.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]
CHART = REPO / "helm" / "fuzeinfra"
POLICY_NAME = "fuzeinfra-edge-egress"
LABEL = "fuzeinfra.io/egress-profile"

# The pods the lockdown exists for, identified by their CHART COMPONENT
# (`app.kubernetes.io/name`, set by the `fuzeinfra.selectorLabels` helper), not by
# `metadata.name`.
#
# That distinction is load-bearing rather than stylistic. This chart is
# inconsistent about the release prefix: 24 workloads render as `fuzeinfra-<x>`
# (`fuzeinfra-postgres`, `fuzeinfra-airflow-webserver`, …) while these three and
# `custom-hostname-api` render bare. Matching on `metadata.name` would therefore
# hard-code a convention only some of the chart follows, and would break — with a
# misleading "workload outside the lockdown" message — the day one of these is
# renamed to match the majority. The component label is the chart's actual
# identity for a workload and is what the NetworkPolicy's own selector is derived
# from, so it stays correct either way.
EDGE_COMPONENTS = {"a2a-relay", "a2a-gateway", "handoff-mcp"}

pytestmark = pytest.mark.skipif(
    shutil.which("helm") is None, reason="helm not installed"
)


def _render(values: str) -> list:
    out = subprocess.run(
        # --namespace is required: the chart refuses to render service addresses
        # into Helm's "default" fallback (messaging.yaml guards Kafka's
        # advertised.listeners against it).
        ["helm", "template", "fuzeinfra", str(CHART),
         "--namespace", "fuzeinfra", "-f", str(CHART / values)],
        capture_output=True, text=True, check=True,
    ).stdout
    return [d for d in yaml.safe_load_all(out) if d]


def _component(doc) -> str | None:
    """A workload's chart component, independent of any release-name prefix."""
    return (doc["spec"]["template"]["metadata"].get("labels") or {}).get(
        "app.kubernetes.io/name"
    )


def _policy(docs):
    found = [
        d for d in docs
        if d.get("kind") == "NetworkPolicy" and d["metadata"]["name"] == POLICY_NAME
    ]
    return found[0] if found else None


# --- the gate ---------------------------------------------------------------

def test_absent_when_gate_is_off():
    """Base values ship it OFF: the `except` CIDRs are cluster-specific, and a
    policy carrying another cluster's ranges either blocks nothing or blocks the
    internet these pods need."""
    assert _policy(_render("values.yaml")) is None


def test_present_in_prod():
    assert _policy(_render("values-contabo.yaml")) is not None


# --- the policy itself ------------------------------------------------------

def test_scope_is_egress_only():
    """Ingress is deliberately NOT filtered. The threat is outbound lateral
    movement; restricting ingress would risk Prometheus scraping and kubelet
    health probes for no security gain."""
    pol = _policy(_render("values-contabo.yaml"))
    assert pol["spec"]["policyTypes"] == ["Egress"]


def test_selects_on_the_opt_in_label():
    pol = _policy(_render("values-contabo.yaml"))
    assert pol["spec"]["podSelector"]["matchLabels"] == {LABEL: "edge"}


def test_dns_is_allowed():
    """Without an explicit DNS rule the pods cannot resolve anything — including
    the public internet they are still permitted to reach."""
    pol = _policy(_render("values-contabo.yaml"))
    dns = [
        r for r in pol["spec"]["egress"]
        if any(p.get("port") == 53 for p in r.get("ports", []))
    ]
    assert dns, "no DNS egress rule — the policy would break every lookup"
    protocols = {p["protocol"] for p in dns[0]["ports"]}
    assert protocols == {"UDP", "TCP"}, f"DNS must allow both, got {protocols}"


def test_cluster_ranges_are_excluded_and_nonempty():
    """THE load-bearing assertion. `except: []` renders a perfectly valid
    NetworkPolicy that permits 0.0.0.0/0 — i.e. blocks nothing at all."""
    pol = _policy(_render("values-contabo.yaml"))
    block = [
        r["to"][0]["ipBlock"] for r in pol["spec"]["egress"]
        if r.get("to") and "ipBlock" in r["to"][0]
    ][0]
    assert block["cidr"] == "0.0.0.0/0"
    excepts = block.get("except") or []
    assert excepts, "empty `except` — the policy blocks nothing"
    # Pod, service and node-VLAN ranges must all be covered. Missing the VLAN
    # would let an edge pod reach a datastore by node IP instead of Service IP.
    assert any(e.startswith("10.42.") for e in excepts), f"pod CIDR missing: {excepts}"
    assert any(e.startswith("10.43.") for e in excepts), f"service CIDR missing: {excepts}"
    assert any(e.startswith("10.0.") for e in excepts), f"node VLAN missing: {excepts}"


# --- the pods it governs ----------------------------------------------------

def test_every_edge_workload_carries_the_label():
    """A Deployment that loses the label silently leaves the policy's scope while
    the policy still renders correctly."""
    docs = _render("values-contabo.yaml")
    labelled = {
        _component(d) for d in docs
        if d.get("kind") == "Deployment"
        and (d["spec"]["template"]["metadata"].get("labels") or {}).get(LABEL) == "edge"
    }
    missing = EDGE_COMPONENTS - labelled
    assert not missing, f"internet-facing workloads outside the lockdown: {sorted(missing)}"


def test_no_datastore_accidentally_joins_the_lockdown():
    """The inverse mistake: labelling a datastore would cut it off from the
    cluster it serves."""
    docs = _render("values-contabo.yaml")
    labelled = {
        _component(d) for d in docs
        if d.get("kind") in {"Deployment", "StatefulSet"}
        and (d["spec"]["template"]["metadata"].get("labels") or {}).get(LABEL) == "edge"
    }
    assert labelled <= EDGE_COMPONENTS, (
        f"unexpected pods in the lockdown: {sorted(labelled - EDGE_COMPONENTS)}"
    )


# --- the enforcement probe --------------------------------------------------

def test_probe_is_governed_by_the_same_policy():
    """The probe only proves anything if the policy actually applies to it."""
    docs = _render("values-contabo.yaml")
    jobs = [
        d for d in docs
        if d.get("kind") == "Job" and d["metadata"]["name"].endswith("edge-egress-probe")
    ]
    assert jobs, "no enforcement probe rendered"
    labels = jobs[0]["spec"]["template"]["metadata"].get("labels") or {}
    assert labels.get(LABEL) == "edge"


def test_probe_asserts_both_directions():
    """A probe that only checks the blocked target would pass identically if the
    pod had no network at all."""
    docs = _render("values-contabo.yaml")
    job = [
        d for d in docs
        if d.get("kind") == "Job" and d["metadata"]["name"].endswith("edge-egress-probe")
    ][0]
    script = json.dumps(job["spec"]["template"]["spec"]["containers"][0]["args"])
    assert "blocked-target" in script
    assert "allowed-target" in script


def test_edge_workloads_expose_the_component_label_the_other_tests_key_on():
    """Guards the guard: if `app.kubernetes.io/name` ever stops being emitted,
    `_component()` returns None and the two tests above would compare against
    {None} — passing or failing for reasons unrelated to the lockdown."""
    docs = _render("values-contabo.yaml")
    edge = [
        d for d in docs
        if d.get("kind") == "Deployment"
        and (d["spec"]["template"]["metadata"].get("labels") or {}).get(LABEL) == "edge"
    ]
    assert edge, "no edge-labelled Deployments rendered at all"
    for d in edge:
        assert _component(d), (
            f"{d['metadata']['name']} carries the egress-profile label but no "
            "app.kubernetes.io/name — the component-keyed assertions would silently "
            "compare against None"
        )
