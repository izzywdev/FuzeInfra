"""kube-state-metrics' CustomResourceState collector needs CRD read RBAC, not
just CR read RBAC, to resolve the Longhorn Volume GVK it watches.

Why this file exists
---------------------
`helm/fuzeinfra/files/ksm-customresource-state.yaml` configures KSM's
CustomResourceState collector to watch the Longhorn `Volume` CR
(longhorn.io/v1beta2) so `rules/storage.yml`'s LonghornVolumeUnderReplicated
and LonghornVolumeDegradedTooLong rules have something to alert on (Longhorn's
own /metrics has no replica-count series at all).

The chart granted `list`/`watch` on the CR itself (`apiGroups: ["longhorn.io"],
resources: [volumes]`) but not on the CRD that DEFINES it. The collector
resolves the GVK against the CRD before it can watch anything, so without a
`customresourcedefinitions.apiextensions.k8s.io` grant it failed cluster-scope
RBAC, the `longhorn_crd_volume_*` series never appeared, and nothing surfaced
it anywhere a dashboard would show it: KSM stayed `Running`/scraped (`up=1`)
and the alert rules loaded with `health=ok` (an expression that parses, not
one that has data). See FuzeInfra#1049.

What is enforced here
----------------------
1. When `kubeStateMetrics.longhornVolumeMetrics.enabled=true`, the rendered
   ClusterRole grants `list`+`watch` on BOTH `longhorn.io/volumes` (the CR)
   AND `apiextensions.k8s.io/customresourcedefinitions` (the CRD) — the
   second is the fix; the first is the pre-existing grant this guards against
   regressing alongside it.
2. When the flag is `false` (the chart default), neither rule is present —
   the CRD-read grant must stay coupled to the feature that needs it and not
   leak into every install.
3. The CRS config's watched GVK (`files/ksm-customresource-state.yaml`) is
   still `longhorn.io`, so the RBAC `apiGroups` actually matches what is
   watched (catches a rename on one side going unnoticed on the other).

Offline: `helm template` + parses the rendered YAML. No cluster, no network.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
CHART = REPO_ROOT / "helm" / "fuzeinfra"
CRS_CONFIG = CHART / "files" / "ksm-customresource-state.yaml"

def _render(extra_set: list[str]) -> list[dict]:
    cmd = [
        "helm", "template", "fuzeinfra", str(CHART),
        "-n", "fuzeinfra",
        "-s", "templates/kube-state-metrics.yaml",
        *extra_set,
    ]
    out = subprocess.run(cmd, capture_output=True, text=True, check=True).stdout
    return [doc for doc in yaml.safe_load_all(out) if doc]


def _cluster_role(docs: list[dict]) -> dict:
    roles = [d for d in docs if d.get("kind") == "ClusterRole"]
    assert len(roles) == 1, f"expected exactly one ClusterRole, found {len(roles)}"
    return roles[0]


def _has_rule(role: dict, api_group: str, resource: str, verbs: set[str]) -> bool:
    for rule in role.get("rules", []):
        if api_group in rule.get("apiGroups", []) and resource in rule.get(
            "resources", []
        ):
            if verbs.issubset(set(rule.get("verbs", []))):
                return True
    return False


@pytest.mark.skipif(shutil.which("helm") is None, reason="helm not installed")
def test_crd_read_rbac_granted_when_longhorn_volume_metrics_enabled():
    docs = _render(["--set", "kubeStateMetrics.longhornVolumeMetrics.enabled=true"])
    role = _cluster_role(docs)

    assert _has_rule(role, "longhorn.io", "volumes", {"list", "watch"}), (
        "expected list+watch on longhorn.io/volumes (the CR) when "
        "longhornVolumeMetrics.enabled=true"
    )
    assert _has_rule(
        role, "apiextensions.k8s.io", "customresourcedefinitions", {"list", "watch"}
    ), (
        "kube-state-metrics' CustomResourceState collector needs list+watch on "
        "apiextensions.k8s.io/customresourcedefinitions to resolve the Longhorn "
        "Volume GVK before it can watch the CR — without it the "
        "longhorn_crd_volume_* series never appear, silently (#1049)."
    )


@pytest.mark.skipif(shutil.which("helm") is None, reason="helm not installed")
def test_crd_read_rbac_absent_when_longhorn_volume_metrics_disabled():
    docs = _render(["--set", "kubeStateMetrics.longhornVolumeMetrics.enabled=false"])
    role = _cluster_role(docs)

    assert not _has_rule(role, "longhorn.io", "volumes", {"list", "watch"}), (
        "longhorn.io/volumes RBAC leaked out despite longhornVolumeMetrics.enabled=false"
    )
    assert not _has_rule(
        role, "apiextensions.k8s.io", "customresourcedefinitions", {"list", "watch"}
    ), (
        "customresourcedefinitions RBAC must stay coupled to "
        "longhornVolumeMetrics.enabled — it leaked into a default install."
    )


def test_crs_config_still_watches_the_longhorn_api_group():
    """Catches the RBAC and the CRS config silently drifting apart (rename etc)."""
    spec = yaml.safe_load(CRS_CONFIG.read_text(encoding="utf-8"))
    groups = {
        r["groupVersionKind"]["group"] for r in spec["spec"]["resources"]
    }
    assert groups == {"longhorn.io"}, (
        f"{CRS_CONFIG.name} now watches group(s) {sorted(groups)}; the "
        "kubeStateMetrics.longhornVolumeMetrics RBAC block in "
        "templates/kube-state-metrics.yaml grants longhorn.io/volumes "
        "specifically and needs to move with it."
    )
