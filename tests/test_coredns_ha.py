"""Executable invariants for the extra CoreDNS replicas (argocd/cluster-bootstrap/coredns-ha.yaml).

This file exists because the previous version of that manifest was INERT and nobody
noticed for a year. It was a `HelmChartConfig` named `coredns`, which only overrides a
k3s `HelmChart` — and CoreDNS is not a HelmChart here, it is an Addon applied from
/var/lib/rancher/k3s/server/manifests/coredns.yaml. Nothing errored, nothing applied,
and `spec.replicas` stayed 1 while the repo read as if DNS were highly available. The
outage in FuzeInfra#1187 is what finally surfaced it.

So the first test below is a regression guard on the SHAPE of the fix, not on its
wording: if this ever becomes a HelmChartConfig again, it fails.

The rest encode the four properties that make this manifest do its job, each of which
is silently satisfiable-looking and wrong if you get it backwards:

  1. The pods must carry `k8s-app: kube-dns`, because that label -- and only that
     label -- is what the existing kube-dns Service selects. Without it the pods run
     happily, look healthy, serve nobody, and DNS is still single-homed.

  2. The Deployment's SELECTOR must NOT be `k8s-app: kube-dns`. That is the dangerous
     inverse of (1): a selector that broad would make this Deployment try to adopt the
     k3s-owned CoreDNS pod, and two controllers fighting over cluster DNS is a far
     worse outage than the one being fixed.

  3. It must tolerate the worker taints and stay OFF the control-plane. Every node in
     this cluster is tainted, and the k3s CoreDNS tolerates only the control-plane
     one -- which is exactly why, when the CI nodes lost their overlay to all three
     control-plane nodes, CI pods had no reachable resolver at all. Replicas without
     these placement rules would land back on the unreachable island and fix nothing.

  4. `dnsPolicy: Default`. CoreDNS must not resolve through cluster DNS, because it
     IS cluster DNS.

Offline: parses YAML. No cluster, no network.
"""

import pathlib

import pytest
import yaml

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
MANIFEST = REPO_ROOT / "argocd" / "cluster-bootstrap" / "coredns-ha.yaml"
BOOTSTRAP_DIR = REPO_ROOT / "argocd"

# The Service selector this manifest has to satisfy. Sourced from the live cluster:
#   kubectl -n kube-system get svc kube-dns -o jsonpath='{.spec.selector}'
KUBE_DNS_SELECTOR = {"k8s-app": "kube-dns"}

# Taints carried by the non-control-plane nodes.
WORKER_TAINTS = {"fuzeinfra.io/ci", "fuzeinfra.io/elastic"}


@pytest.fixture(scope="module")
def deployment():
    assert MANIFEST.is_file(), f"{MANIFEST} is missing"
    doc = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))
    assert doc, f"{MANIFEST} parsed to nothing"
    return doc


@pytest.fixture(scope="module")
def pod_spec(deployment):
    return deployment["spec"]["template"]["spec"]


def test_is_a_deployment_not_an_inert_helmchartconfig(deployment):
    """The original bug: a HelmChartConfig for a chart that does not exist."""
    assert deployment["kind"] == "Deployment", (
        f"expected a Deployment, got {deployment['kind']!r}. A HelmChartConfig named "
        "'coredns' does NOT work on k3s -- CoreDNS is an Addon from a static manifest, "
        "not a HelmChart, so the override applies to nothing and fails silently."
    )
    assert deployment["metadata"]["name"] == "coredns-ha"
    assert deployment["metadata"]["namespace"] == "kube-system"


def test_no_helmchartconfig_anywhere_targets_coredns():
    """Guard the whole tree, not just this file -- the inert form could reappear elsewhere."""
    offenders = []
    for path in BOOTSTRAP_DIR.rglob("*.yaml"):
        try:
            docs = list(yaml.safe_load_all(path.read_text(encoding="utf-8")))
        except yaml.YAMLError:
            continue
        for doc in docs:
            if not isinstance(doc, dict):
                continue
            if doc.get("kind") == "HelmChartConfig" and (
                doc.get("metadata", {}).get("name") == "coredns"
            ):
                offenders.append(str(path.relative_to(REPO_ROOT)))
    assert not offenders, (
        "HelmChartConfig named 'coredns' found in: "
        + ", ".join(offenders)
        + ". k3s has no 'coredns' HelmChart (only traefik/traefik-crd), so this silently "
        "does nothing."
    )


def test_pods_join_the_existing_kube_dns_service(deployment):
    """Without this label the replicas serve no traffic at all."""
    labels = deployment["spec"]["template"]["metadata"]["labels"]
    for key, value in KUBE_DNS_SELECTOR.items():
        assert labels.get(key) == value, (
            f"pod template must carry {key}={value} or the kube-dns Service will never "
            f"select these pods; got {labels!r}"
        )


def test_selector_does_not_adopt_the_k3s_owned_pods(deployment):
    """The dangerous inverse: two controllers fighting over cluster DNS."""
    selector = deployment["spec"]["selector"]["matchLabels"]
    assert selector != KUBE_DNS_SELECTOR, (
        "selector is exactly the kube-dns Service selector, so this Deployment would "
        "try to ADOPT the k3s-managed CoreDNS pod. Select on a label unique to this "
        "Deployment instead."
    )
    assert "k8s-app" not in selector, (
        f"selector must not key on k8s-app (it would match the k3s pods): {selector!r}"
    )
    # It still has to select its own pods.
    pod_labels = deployment["spec"]["template"]["metadata"]["labels"]
    for key, value in selector.items():
        assert pod_labels.get(key) == value, (
            f"selector {key}={value} does not match the pod template labels {pod_labels!r}"
        )


def test_tolerates_the_worker_taints(pod_spec):
    tolerated = {t.get("key") for t in pod_spec.get("tolerations", [])}
    missing = WORKER_TAINTS - tolerated
    assert not missing, (
        f"missing tolerations for {sorted(missing)}. Every node in this cluster is "
        "tainted; without these the replicas are unschedulable anywhere except the "
        "control-plane -- the island this manifest exists to stop depending on."
    )


def test_stays_off_the_control_plane(pod_spec):
    terms = (
        pod_spec.get("affinity", {})
        .get("nodeAffinity", {})
        .get("requiredDuringSchedulingIgnoredDuringExecution", {})
        .get("nodeSelectorTerms", [])
    )
    expressions = [e for term in terms for e in term.get("matchExpressions", [])]
    assert any(
        e.get("key") == "node-role.kubernetes.io/control-plane"
        and e.get("operator") == "DoesNotExist"
        for e in expressions
    ), (
        "required nodeAffinity must exclude control-plane nodes. The k3s-owned CoreDNS "
        "already covers that island; a replica there is unreachable from a partitioned "
        "worker, which is the failure mode being fixed."
    )


def test_spreads_across_hosts(deployment, pod_spec):
    assert deployment["spec"]["replicas"] >= 2, "a single replica is not redundancy"
    constraints = pod_spec.get("topologySpreadConstraints", [])
    host = [c for c in constraints if c.get("topologyKey") == "kubernetes.io/hostname"]
    assert host, "no per-host topology spread constraint; replicas could share one node"
    assert host[0].get("whenUnsatisfiable") == "DoNotSchedule", (
        "per-host spread must be DoNotSchedule; ScheduleAnyway permits both replicas "
        "on one node, which is the failure being designed out"
    )


def test_coredns_does_not_resolve_through_itself(pod_spec):
    assert pod_spec.get("dnsPolicy") == "Default", (
        "CoreDNS must use dnsPolicy Default -- it IS cluster DNS, so ClusterFirst "
        "would point it at itself"
    )


def test_readiness_gated_so_a_bad_rollout_serves_nothing(deployment):
    """The safety property: endpoints only receive traffic once Ready."""
    container = deployment["spec"]["template"]["spec"]["containers"][0]
    probe = container.get("readinessProbe", {}).get("httpGet", {})
    assert probe.get("path") == "/ready" and probe.get("port") == 8181, (
        "readiness must probe CoreDNS /ready:8181. Without it a replica that cannot "
        "load its Corefile still joins the Service and black-holes real DNS queries."
    )


def test_shares_the_k3s_configmaps_rather_than_forking_them(pod_spec):
    """A second, drifting DNS config is worse than one fewer replica."""
    configmaps = {
        v["configMap"]["name"] for v in pod_spec["volumes"] if "configMap" in v
    }
    assert {"coredns", "coredns-custom"} <= configmaps, (
        f"expected the live 'coredns' and 'coredns-custom' ConfigMaps, got {configmaps!r}"
    )
