"""Workload-identity authenticator RBAC, and the fuzeinfra-prod destination guard.

`helm/fuzeinfra/templates/workload-identity-rbac.yaml` provisions the
cluster-scoped half of FuzeFront's workload authentication: a TokenReview grant,
plus ConfigMap read confined to the namespaces that hold identity declarations.

What FuzeFront#1198 shipped instead was a ClusterRole granting `configmaps
get,list` CLUSTER-WIDE, rendered by a consumer chart whose AppProject forbids
ClusterRoles. That froze every FuzeFront deploy (#1278). These tests pin the
least-privilege shape so the wide grant cannot quietly come back.

The last test is broader than this feature: it renders the prod overlay and
asserts every namespaced object lands in a destination the `fuzeinfra`
AppProject allows. Rendering into any other namespace fails the WHOLE
fuzeinfra-prod sync (FuzeInfra#668 froze all prod deploys that way), and no
test caught it then.

Offline: `helm template` only. No cluster, no network.
"""

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]
CHART = REPO / "helm" / "fuzeinfra"
PROJECT = REPO / "argocd" / "projects" / "fuzeinfra.yaml"
NAME = "fuzefront-workload-authenticator"
WAVE = "argocd.argoproj.io/sync-wave"

pytestmark = pytest.mark.skipif(
    shutil.which("helm") is None, reason="helm not installed"
)


def _render(values=None, extra=()):
    cmd = ["helm", "template", "fuzeinfra", str(CHART), "--namespace", "fuzeinfra"]
    if values:
        cmd += ["-f", str(CHART / values)]
    cmd += list(extra)
    out = subprocess.run(cmd, capture_output=True, text=True, check=True).stdout
    return [d for d in yaml.safe_load_all(out) if d]


def _ours(docs):
    return {
        (d["kind"], d["metadata"].get("namespace")): d
        for d in docs
        if d["metadata"]["name"] in (NAME, f"{NAME}-registry")
    }


@pytest.fixture(scope="module")
def prod():
    return _render("values-contabo.yaml")


def test_base_values_render_nothing():
    """Off by default: only an overlay that names an authenticator gets RBAC."""
    assert not _ours(_render())


def test_prod_renders_the_adopting_names(prod):
    """The names must match the live objects FuzeFront#1198 created.

    That match is the adoption: fuzeinfra-prod takes them over and narrows them
    on sync. A different name would leave the wide originals live and orphaned,
    still tracked by the `fuzefront` app, which would keep trying to prune them
    and keep its own sync failing.
    """
    assert set(_ours(prod)) == {
        ("ClusterRole", None),
        ("ClusterRoleBinding", None),
        ("Role", "fuzefront"),
        ("RoleBinding", "fuzefront"),
    }


def test_cluster_role_grants_tokenreview_and_nothing_else(prod):
    rules = _ours(prod)[("ClusterRole", None)]["rules"]
    assert rules == [{
        "apiGroups": ["authentication.k8s.io"],
        "resources": ["tokenreviews"],
        "verbs": ["create"],
    }], (
        "the authenticator's ClusterRole must be TokenReview-only. FuzeFront#1198 "
        "also granted cluster-wide `configmaps get,list` (every ConfigMap in "
        "kube-system, argocd, fuzeinfra) when the service only reads its caller's "
        f"namespace. Got: {rules}"
    )


def test_configmap_read_is_namespaced(prod):
    ours = _ours(prod)
    role = ours[("Role", "fuzefront")]
    assert role["rules"] == [{
        "apiGroups": [""], "resources": ["configmaps"], "verbs": ["get", "list"],
    }]
    binding = ours[("RoleBinding", "fuzefront")]
    assert binding["roleRef"] == {
        "apiGroup": "rbac.authorization.k8s.io", "kind": "Role", "name": f"{NAME}-registry",
    }, "must bind the namespaced Role — a RoleBinding to a ClusterRole would be easy to widen"


def test_bindings_name_exactly_the_security_service(prod):
    subject = [{"kind": "ServiceAccount", "name": "fuzefront-security", "namespace": "fuzefront"}]
    ours = _ours(prod)
    assert ours[("ClusterRoleBinding", None)]["subjects"] == subject
    assert ours[("RoleBinding", "fuzefront")]["subjects"] == subject


def test_registry_read_lands_before_cluster_read_is_dropped(prod):
    """Adopting narrows the live ClusterRole, removing cluster-wide ConfigMap read.

    The namespaced Role has to be in place first, or the authenticator spends
    the gap unable to read its registry and rejects every workload token.
    """
    ours = _ours(prod)
    role_wave = int(ours[("Role", "fuzefront")]["metadata"]["annotations"][WAVE])
    binding_wave = int(ours[("RoleBinding", "fuzefront")]["metadata"]["annotations"][WAVE])
    cluster_wave = int(ours[("ClusterRole", None)]["metadata"]["annotations"][WAVE])
    assert max(role_wave, binding_wave) < cluster_wave


def test_missing_required_fields_fail_the_render():
    with pytest.raises(subprocess.CalledProcessError):
        _render(extra=["--set", "workloadIdentity.authenticators[0].name=x"])


def _project_destinations():
    project = yaml.safe_load(PROJECT.read_text())
    return {d["namespace"] for d in project["spec"]["destinations"]}


def test_every_prod_object_lands_in_an_allowed_destination(prod):
    """The guard FuzeInfra#668 needed and never had.

    Argo validates every task before applying any, so ONE object in a namespace
    the `fuzeinfra` project does not list fails the whole fuzeinfra-prod sync,
    not just that object. Adding a registryNamespaces entry, a routeProfile, or
    anything else that renders cross-namespace must come with a destination.
    """
    allowed = _project_destinations()
    stray = sorted(
        f'{d["kind"]}/{d["metadata"]["name"]} -> {d["metadata"]["namespace"]}'
        for d in prod
        if d["metadata"].get("namespace") and d["metadata"]["namespace"] not in allowed
    )
    assert not stray, (
        "these prod objects render into namespaces argocd/projects/fuzeinfra.yaml "
        f"does not allow, which fails the ENTIRE fuzeinfra-prod sync: {stray}"
    )


def test_destination_guard_catches_an_unlisted_registry_namespace():
    """Proves the guard above can fail, using the exact change it protects."""
    docs = _render("values-contabo.yaml", extra=[
        "--set", "workloadIdentity.authenticators[0].registryNamespaces={fuzefront,fuzesocial}",
    ])
    allowed = _project_destinations()
    assert "fuzesocial" not in allowed, "fixture assumption broke: pick another namespace"
    assert any(d["metadata"].get("namespace") == "fuzesocial" for d in docs)
