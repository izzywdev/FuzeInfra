"""Headlamp must stay a READ-ONLY window onto prod.

argocd/applications/headlamp.yaml runs Headlamp with
`config.unsafeUseServiceAccountToken: true`, so every viewer who gets past
Cloudflare Access acts as the pod's ServiceAccount. Whatever that SA can do,
anyone on the Access allowlist can do from a browser. Prod is GitOps with no
mutate path for any session or human UI (CLAUDE.md, "GitOps + self-heal"), so
the SA must hold read verbs only and no Secret access -- the same policy
cluster-query.yml enforces.

The upstream chart binds its SA to `cluster-admin` by default; one missing
`clusterRoleBinding.create: false` silently turns this UI into a cluster-admin
console. These checks parse the committed manifest only: no helm, no network.
"""

from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

ROOT = Path(__file__).parents[1]
APP = ROOT / "argocd" / "applications" / "headlamp.yaml"
CLOUDFLARE_TF = ROOT / "terraform" / "contabo" / "cloudflare.tf"

READ_VERBS = {"get", "list", "watch"}
# Roles that grant writes or Secret reads. `view` is the only built-in allowed.
FORBIDDEN_ROLE_REFS = {"cluster-admin", "admin", "edit"}
FORBIDDEN_RESOURCES = {"secrets", "pods/exec", "pods/attach", "pods/portforward",
                       "serviceaccounts/token", "*"}


@pytest.fixture(scope="module")
def app():
    return yaml.safe_load(APP.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def values(app):
    return yaml.safe_load(app["spec"]["source"]["helm"]["values"])


@pytest.fixture(scope="module")
def extra(values):
    return [yaml.safe_load(m) for m in values.get("extraManifests", [])]


def test_chart_default_cluster_admin_binding_is_off(values):
    assert values["clusterRoleBinding"]["create"] is False, (
        "The chart's own ClusterRoleBinding defaults to cluster-admin; it must stay "
        "disabled. Bind the SA in extraManifests instead."
    )


def test_bindings_reference_only_read_roles(extra):
    own_roles = {d["metadata"]["name"] for d in extra if d["kind"] == "ClusterRole"}
    bindings = [d for d in extra if d["kind"] in ("ClusterRoleBinding", "RoleBinding")]
    assert bindings, "Headlamp SA has no binding at all"
    for b in bindings:
        ref = b["roleRef"]["name"]
        assert ref not in FORBIDDEN_ROLE_REFS, f"{b['metadata']['name']} binds {ref}"
        assert ref == "view" or ref in own_roles, (
            f"{b['metadata']['name']} binds {ref!r}, which this test cannot audit; "
            "only the built-in `view` or a ClusterRole defined in this manifest is allowed"
        )


def test_custom_roles_are_read_only_and_secret_free(extra):
    roles = [d for d in extra if d["kind"] in ("ClusterRole", "Role")]
    assert roles
    for role in roles:
        assert "aggregationRule" not in role, "aggregated roles hide their real rules"
        for rule in role["rules"]:
            verbs = set(rule["verbs"])
            assert verbs <= READ_VERBS, f"{role['metadata']['name']}: non-read verbs {verbs - READ_VERBS}"
            bad = set(rule["resources"]) & FORBIDDEN_RESOURCES
            assert not bad, f"{role['metadata']['name']}: forbidden resources {bad}"
            assert "*" not in rule.get("apiGroups", []), "wildcard apiGroups would include Secrets"
            assert not rule.get("nonResourceURLs"), "nonResourceURLs not allowed"


def test_service_account_mode_only_behind_access_wall(values):
    # The SA-token mode is only acceptable because the host is under the
    # *.prod wildcard Cloudflare Access app and reachable only via the tunnel.
    assert values["config"]["unsafeUseServiceAccountToken"] is True
    hosts = [h["host"] for h in values["ingress"]["hosts"]]
    assert hosts == ["headlamp.prod.fuzefront.com"]


def test_launcher_tile_present():
    assert '"headlamp"' in CLOUDFLARE_TF.read_text(encoding="utf-8")
