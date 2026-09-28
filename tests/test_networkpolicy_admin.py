"""The admin-plane ingress lockdown must stay a lockdown.

`helm/fuzeinfra/templates/networkpolicy-admin.yaml` restricts the admin and
observability UIs to this namespace plus Traefik. Before it, every Service in
`fuzeinfra` was ClusterIP on a pod network flat with 24 other namespaces, so a pod
in any of them could open prometheus:9090 (unauthenticated, with
--web.enable-admin-api, so delete_series was callable), alertmanager:9093
(unauthenticated — a POSTed silence blinds the alerting added in #1175),
mongo-express:8081 (a MongoDB admin UI in a pod holding the real Mongo
credentials), grafana:3000, kafka-ui:8080 or flower:5555 — every one of them
bypassing the Cloudflare Access wall that guards these hosts from the internet.

Each assertion guards a way this can silently stop protecting while everything
still looks correct and nothing goes red:

  * dropping a workload's opt-in label removes it from the policy, and the policy
    object itself still renders perfectly
  * adding Egress to policyTypes would start filtering these pods' OUTBOUND
    traffic — Prometheus scraping every target, Grafana reaching its datasources —
    which this policy deliberately does not do
  * losing the Traefik rule takes every admin UI off the internet, turning a
    security change into an outage
  * a bare kube-system namespaceSelector, without the Traefik podSelector, would
    admit every system pod instead of just the ingress controller
  * labelling a datastore or a consumer-facing service would cut live consumer
    apps off from it

Offline: renders the chart with `helm template`. No cluster, no network.
"""

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]
CHART = REPO / "helm" / "fuzeinfra"
POLICY_NAME = "fuzeinfra-admin-ingress"
LABEL = "fuzeinfra.io/ingress-profile"

# The protected set, identified by CHART COMPONENT (`app.kubernetes.io/name`) and
# never by `metadata.name`. The chart is inconsistent about the release prefix —
# most workloads render as `fuzeinfra-<x>` but several render bare — so keying on
# metadata.name would encode a convention only part of the chart follows and would
# break, with a misleading message, the day one is renamed. This mirrors
# tests/test_networkpolicy_edge.py, where that exact problem was caught in review.
ADMIN_COMPONENTS = {
    "prometheus",
    "alertmanager",
    "grafana",
    "mongo-express",
    "kafka-ui",
    "airflow-flower",
}

# Services that MUST stay reachable across namespaces. Labelling any of these
# would break live consumer apps, which is the AC1.3 regression this scope avoids:
#   elasticsearch     advertised to consumers in NOTES.txt, aliased in CoreDNS
#   airflow-webserver AIRFLOW__API__AUTH_BACKENDS=basic_auth — apps may trigger DAGs
#   loki / tempo      documented otel-collector-only, but unmeasured
#   the datastores    the hot path every consumer depends on
MUST_NOT_BE_LOCKED = {
    "elasticsearch",
    "airflow-webserver",
    "loki",
    "tempo",
    "postgres",
    "redis",
    "mongodb",
    "neo4j",
    "kafka",
    "rabbitmq",
    "chromadb",
    "otel-collector",
}

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


def _workloads(docs):
    return [d for d in docs
            if d.get("kind") in ("Deployment", "StatefulSet", "DaemonSet")]


def _policy(docs):
    found = [d for d in docs
             if d.get("kind") == "NetworkPolicy"
             and d["metadata"]["name"] == POLICY_NAME]
    return found[0] if found else None


def _opted_in(docs) -> set:
    return {
        _component(d) for d in _workloads(docs)
        if (d["spec"]["template"]["metadata"].get("labels") or {}).get(LABEL) == "admin"
    }


# --- the gate ---------------------------------------------------------------

def test_absent_when_gate_is_off():
    """Base values ship it OFF, so kind and EKS are unaffected until they opt in."""
    assert _policy(_render("values.yaml")) is None


def test_present_in_prod():
    assert _policy(_render("values-contabo.yaml")) is not None


def test_gate_is_independent_of_the_egress_gate():
    """adminPlane.enabled must not be wired to networkPolicy.enabled. The egress
    policy's correctness depends on cluster-specific CIDRs; this one's does not, so
    an overlay must be able to take this without first getting those right."""
    values = yaml.safe_load((CHART / "values.yaml").read_text())
    np = values["networkPolicy"]
    assert np["enabled"] is False
    assert np["adminPlane"]["enabled"] is False, (
        "both default off, but they must be SEPARATE keys"
    )
    assert "adminPlane" in np and isinstance(np["adminPlane"], dict)


# --- the policy itself ------------------------------------------------------

def test_scope_is_ingress_only():
    """Egress is deliberately NOT filtered. Prometheus must keep scraping every
    target in the cluster and Grafana must keep reaching its datasources; adding
    Egress here would break both for no gain."""
    pol = _policy(_render("values-contabo.yaml"))
    assert pol["spec"]["policyTypes"] == ["Ingress"]


def test_selects_on_the_opt_in_label():
    pol = _policy(_render("values-contabo.yaml"))
    assert pol["spec"]["podSelector"]["matchLabels"] == {LABEL: "admin"}


def test_same_namespace_is_allowed():
    """Without this rule the observability plane cuts itself: Prometheus scrapes
    Grafana and Alertmanager, Grafana queries Prometheus, Prometheus posts alerts
    to Alertmanager, and the backup CronJob calls Prometheus's snapshot API."""
    pol = _policy(_render("values-contabo.yaml"))
    rules = pol["spec"]["ingress"]
    assert any(
        any(src == {"podSelector": {}} for src in rule.get("from", []))
        for rule in rules
    ), "no rule admits this namespace's own pods"


def test_traefik_is_allowed_and_is_scoped_to_traefik():
    """Traefik is the only route that has passed Cloudflare Access. Losing the rule
    takes every admin UI off the internet; widening it to bare kube-system would
    admit every system pod."""
    pol = _policy(_render("values-contabo.yaml"))
    matched = []
    for rule in pol["spec"]["ingress"]:
        for src in rule.get("from", []):
            ns = src.get("namespaceSelector", {}).get("matchLabels", {})
            if ns.get("kubernetes.io/metadata.name") == "kube-system":
                matched.append(src)
    assert matched, "no rule admits Traefik — the admin UIs would be unreachable"
    for src in matched:
        pod = src.get("podSelector", {}).get("matchLabels", {})
        assert pod.get("app.kubernetes.io/name") == "traefik", (
            "a kube-system rule without a Traefik podSelector admits every "
            f"system pod: {src}"
        )


def test_no_extra_namespaces_are_granted_in_prod():
    """Each entry grants admin-plane access to EVERY pod in that namespace, so an
    accidental addition is a real widening. Prod ships none."""
    values = yaml.safe_load((CHART / "values-contabo.yaml").read_text())
    assert values["networkPolicy"]["adminPlane"]["extraAllowedNamespaces"] == []


def test_extra_namespaces_render_when_set(tmp_path):
    """The escape hatch has to actually work, or the only way out of a false
    positive is deleting a workload's label — which drops protection entirely."""
    overlay = tmp_path / "values-extra.yaml"
    overlay.write_text(
        "networkPolicy:\n"
        "  adminPlane:\n"
        "    enabled: true\n"
        "    extraAllowedNamespaces: [fuzeplan]\n"
    )
    out = subprocess.run(
        ["helm", "template", "fuzeinfra", str(CHART), "--namespace", "fuzeinfra",
         "-f", str(CHART / "values-contabo.yaml"), "-f", str(overlay)],
        capture_output=True, text=True, check=True,
    ).stdout
    pol = _policy([d for d in yaml.safe_load_all(out) if d])
    granted = {
        src.get("namespaceSelector", {}).get("matchLabels", {}).get(
            "kubernetes.io/metadata.name")
        for rule in pol["spec"]["ingress"] for src in rule.get("from", [])
    }
    assert "fuzeplan" in granted


# --- the protected set ------------------------------------------------------

def test_exactly_the_intended_workloads_opt_in():
    """Both directions matter. A missing component is an unprotected admin UI; an
    extra one may be a service consumers depend on, now cut off."""
    assert _opted_in(_render("values-contabo.yaml")) == ADMIN_COMPONENTS


def test_consumer_facing_services_are_never_locked():
    """The AC1.3 regression guard. Each of these is reached across namespaces by
    something outside this chart, so labelling one breaks live consumer apps."""
    opted = _opted_in(_render("values-contabo.yaml"))
    overlap = opted & MUST_NOT_BE_LOCKED
    assert not overlap, (
        f"consumer-facing service(s) pulled into the admin lockdown: {overlap}"
    )


def test_opted_in_workloads_expose_the_component_label_the_tests_key_on():
    """Guard the guard: if `app.kubernetes.io/name` ever stopped being set on these
    pod templates, `_component()` would return None and the set comparison above
    would fail confusingly rather than pointing here."""
    docs = _render("values-contabo.yaml")
    unnamed = [
        d["metadata"]["name"] for d in _workloads(docs)
        if (d["spec"]["template"]["metadata"].get("labels") or {}).get(LABEL) == "admin"
        and _component(d) is None
    ]
    assert not unnamed, f"opted-in workloads with no component label: {unnamed}"


def test_the_label_is_only_on_pod_templates_not_on_services():
    """The policy selects PODS. A label landing on a Service or on a Deployment's
    top-level metadata protects nothing, and reads as though it does."""
    docs = _render("values-contabo.yaml")
    stray = [
        f"{d['kind']}/{d['metadata']['name']}" for d in docs
        if d.get("kind") not in ("Deployment", "StatefulSet", "DaemonSet", "Job")
        and (d.get("metadata", {}).get("labels") or {}).get(LABEL)
    ]
    assert not stray, f"opt-in label on non-pod object(s): {stray}"
