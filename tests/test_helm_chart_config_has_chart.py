"""Guards against a manifest whose declared intent can NEVER take effect: a k3s
`HelmChartConfig` for a component k3s does not deploy via Helm at all.

Background (2026-09-23). `argocd/cluster-bootstrap/coredns-ha.yaml` was a
`helm.cattle.io/v1 HelmChartConfig` named "coredns" with `replicaCount: 2`,
applied to the cluster (via .github/workflows/apply-cluster-config.yml,
`kubectl apply -f argocd/cluster-bootstrap/`) since 2026-07-29. It did
NOTHING for two months: k3s's helm-controller only reconciles a
HelmChartConfig against a `HelmChart` CR of the SAME NAME, and k3s deploys
CoreDNS as a "packaged manifest" (a static file applied by the deploy
controller, tracked as a `k3s.cattle.io/v1 Addon`), never as a Helm release.
Verified live: `kubectl -n kube-system get helmchart` returns only
`traefik` and `traefik-crd` -- the only two components this k3s cluster
actually deploys via Helm. A HelmChartConfig for anything else is a
manifest that LOOKS like it configures something and silently configures
nothing. `kubectl apply` does not error on an orphaned HelmChartConfig
(it's a perfectly valid CR on its own), so nothing short of reading the
list of live HelmChart CRs would ever have caught this -- which is what
this test does, offline, from a fixed allowlist instead of a live cluster
call (so it runs in every PR, not just when someone happens to check prod).

This is deliberately a small, cheap, offline check per the repo's test
convention (tests/test_a2a_surface.py, tests/test_cluster_query_guard.py):
parse the committed manifests, assert a static invariant, no cluster, no
network, runs in milliseconds.

If a future PR legitimately adds a new k3s-packaged Helm component (rare --
this cluster ships with exactly traefik/traefik-crd out of the box), widen
KNOWN_K3S_PACKAGED_HELM_CHARTS deliberately and note the corresponding
`kubectl -n kube-system get helmchart` verification in the PR, the same way
this file documents today's set.
"""

from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

ROOT = Path(__file__).parents[1]

# The full set of k3s-packaged Helm components this cluster deploys via its
# built-in helm-controller, as observed live 2026-09-23:
#   kubectl -n kube-system get helmchart
#     NAME          CHART
#     traefik       .../traefik-40.1.3+up40.1.0.tgz
#     traefik-crd   .../traefik-crd-40.1.3+up40.1.0.tgz
# A HelmChartConfig is only ever reconciled against a HelmChart CR of the
# SAME NAME (k3s helm-controller behaviour) -- CoreDNS is NOT one of these;
# it is a static "packaged manifest" (k3s.cattle.io/v1 Addon), which is the
# root cause this test exists to catch a repeat of.
KNOWN_K3S_PACKAGED_HELM_CHARTS = {"traefik", "traefik-crd"}

# Directories that hold manifests actually applied straight to the cluster
# (via apply-cluster-config.yml / the ARC gitops Application / etc.), as
# opposed to Helm chart TEMPLATES (helm/*/templates/**), which are Go
# templates, not directly-appliable YAML, and are never eligible to carry a
# HelmChartConfig in the first place (a HelmChartConfig inside a Helm chart
# would be applied once per chart, not a cluster bootstrap manifest -- if
# that ever happens it is its own bug, out of scope here).
RAW_MANIFEST_DIRS = [
    ROOT / "argocd" / "cluster-bootstrap",
    ROOT / "argocd" / "cluster-config",
    ROOT / "argocd" / "cluster-settings",
    ROOT / "runners" / "arc" / "gitops",
]


def _iter_yaml_docs():
    """Yield (path, doc) for every parseable YAML document under the raw
    manifest directories. Files that fail to parse (there should be none --
    these are plain kubectl-apply targets, not Go templates) are skipped
    rather than crashing the test, so this stays a targeted guard and not a
    brittle parser for the whole repo."""
    for d in RAW_MANIFEST_DIRS:
        if not d.is_dir():
            continue
        for path in sorted(d.rglob("*.yaml")) + sorted(d.rglob("*.yml")):
            try:
                docs = yaml.safe_load_all(path.read_text(encoding="utf-8"))
                for doc in docs:
                    if doc:
                        yield path, doc
            except yaml.YAMLError:
                continue


def _helm_chart_configs():
    return [
        (path, doc)
        for path, doc in _iter_yaml_docs()
        if doc.get("apiVersion") == "helm.cattle.io/v1" and doc.get("kind") == "HelmChartConfig"
    ]


def _helm_charts():
    return {
        doc["metadata"]["name"]
        for _, doc in _iter_yaml_docs()
        if doc.get("apiVersion") == "helm.cattle.io/v1" and doc.get("kind") == "HelmChart"
    }


def test_scan_finds_the_known_live_helmchartconfig():
    """Sanity check that the scan itself works before trusting its silence:
    traefik-clusterip.yaml must be found, or every other assertion here is
    passing for the wrong reason (an empty scan)."""
    configs = _helm_chart_configs()
    names = {doc["metadata"]["name"] for _, doc in configs}
    assert "traefik" in names, (
        "expected argocd/cluster-bootstrap/traefik-clusterip.yaml to be found by "
        "the scan -- if this fails, RAW_MANIFEST_DIRS or the YAML parse is broken, "
        "not that traefik config is missing"
    )


def test_every_helmchartconfig_targets_a_component_k3s_actually_helm_deploys():
    """The guard this file exists for. A HelmChartConfig whose name is not in
    KNOWN_K3S_PACKAGED_HELM_CHARTS -- and has no sibling HelmChart CR
    committed alongside it either -- can never be reconciled by k3s's
    helm-controller and is dead on arrival, exactly like the coredns one
    was for two months."""
    committed_charts = _helm_charts()
    offenders = []
    for path, doc in _helm_chart_configs():
        name = doc.get("metadata", {}).get("name")
        namespace = doc.get("metadata", {}).get("namespace")
        if name in KNOWN_K3S_PACKAGED_HELM_CHARTS:
            continue
        if name in committed_charts:
            # A HelmChart CR for it is committed in the same manifest set --
            # legitimate (not k3s-packaged, but self-contained and reconcilable).
            continue
        offenders.append(f"{path.relative_to(ROOT)}: HelmChartConfig '{name}' (namespace={namespace})")

    assert not offenders, (
        "HelmChartConfig(s) with no matching HelmChart -- k3s's helm-controller only "
        "reconciles a HelmChartConfig against a HelmChart CR of the SAME NAME, and "
        "these names are neither a k3s-packaged component "
        f"({sorted(KNOWN_K3S_PACKAGED_HELM_CHARTS)}) nor backed by a committed "
        "HelmChart CR, so they will silently do nothing (this is exactly how "
        "argocd/cluster-bootstrap/coredns-ha.yaml sat inert in prod for 2 months). "
        "Fix: either the component is actually Helm-deployed by k3s (add its name "
        "to KNOWN_K3S_PACKAGED_HELM_CHARTS here, with the `kubectl -n kube-system "
        "get helmchart` output that proves it in the PR), or it needs a different "
        "mechanism entirely (e.g. the node-configurator DaemonSet approach used for "
        "CoreDNS HA in helm/fuzeinfra/templates/node-configurator.yaml). "
        "Offenders:\n  " + "\n  ".join(offenders)
    )


def test_coredns_ha_file_is_gone_not_just_fixed_in_place():
    """The specific regression this whole test file exists to prevent a
    repeat of. Deleted (not edited) so its absence is unambiguous; the real
    fix lives in helm/fuzeinfra/templates/node-configurator.yaml, gated by
    nodeConfigurator.corednsHA."""
    assert not (ROOT / "argocd" / "cluster-bootstrap" / "coredns-ha.yaml").exists()
