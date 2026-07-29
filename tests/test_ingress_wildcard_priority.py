"""Regression guard: a wildcard Ingress host must never outrank an exact host.

Why this file exists
--------------------
Traefik sorts routers by RULE LENGTH, not by host specificity
(https://doc.traefik.io/traefik/reference/routing-configuration/http/routing/rules-and-priority/):

    Router-1  HostRegexp(`[a-z]+\\.traefik\\.com`)   priority 34
    Router-2  Host(`foobar.traefik.com`)             priority 26
    -> Router-1 wins, "despite Router-2 being more specific"

A Kubernetes wildcard host compiles to a longer rule than any exact host in the
same zone, so on Traefik the wildcard silently captures exact hosts — including
hosts owned by OTHER products in the shared cluster, in other namespaces, from
other repositories.

That is not hypothetical. A `*.fuzefront.com` rule added to the FuzeFront chart
captured `plan.fuzefront.com` (FuzePlan, own namespace, own exact-host Ingress)
and served the FuzeFront shell on it until the rule was reverted
(FuzeFront#431 introduced, #437 reverted).

None of the usual checks catch this: the manifest is schema-valid, and the local
overlay uses ingress-nginx, which DOES implement exact-beats-wildcard — so the
rule behaves correctly locally and only misbehaves in prod.

What is enforced here
---------------------
1. Any rendered Ingress carrying a wildcard host must be ALONE in its object and
   must pin a LOW `traefik.ingress.kubernetes.io/router.priority`. The annotation
   is per-object, so a wildcard sharing an object with an exact host cannot be
   de-prioritised without dragging the exact host down too.
2. Every Ingress the custom-hostname API materializes at runtime pins a HIGH
   explicit priority, so a customer's paid custom domain can never be collateral
   damage from someone else's wildcard.

Scope note: this covers FuzeInfra's own chart and its runtime-created objects.
Consumer charts (FuzeFront's Ingress lives in FuzeFront) must mirror rule 1 in
their own repo — FuzeInfra's tests cannot see them.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
CHART = REPO_ROOT / "helm" / "fuzeinfra"
OVERLAYS = ["values.yaml", "values-local.yaml", "values-aws.yaml", "values-contabo.yaml"]

PRIORITY_ANNOTATION = "traefik.ingress.kubernetes.io/router.priority"

#: A wildcard router must sit below every exact-host router. Exact host rules
#: compute to roughly 26-60 by Traefik's length arithmetic, so anything at or
#: below this is unambiguously outranked. Traefik treats 0 as "unset" and falls
#: back to length sorting, so 0 is NOT a valid low value.
MAX_WILDCARD_PRIORITY = 10

SERVICE_ROOT = REPO_ROOT / "services" / "custom-hostname-api"
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))


def _render(overlay: str) -> list[dict]:
    cmd = ["helm", "template", "fuzeinfra", str(CHART), "-n", "fuzeinfra"]
    if overlay != "values.yaml":
        cmd += ["-f", str(CHART / overlay)]
    out = subprocess.run(cmd, capture_output=True, text=True, check=True).stdout
    return [doc for doc in yaml.safe_load_all(out) if doc]


def _wildcard_hosts(ingress: dict) -> list[str]:
    return [
        rule["host"]
        for rule in (ingress.get("spec", {}).get("rules") or [])
        if isinstance(rule.get("host"), str) and "*" in rule["host"]
    ]


def _exact_hosts(ingress: dict) -> list[str]:
    return [
        rule["host"]
        for rule in (ingress.get("spec", {}).get("rules") or [])
        if isinstance(rule.get("host"), str) and "*" not in rule["host"]
    ]


@pytest.mark.skipif(shutil.which("helm") is None, reason="helm not installed")
@pytest.mark.parametrize("overlay", OVERLAYS)
def test_chart_wildcard_ingress_is_isolated_and_deprioritized(overlay):
    """No chart Ingress may ship a wildcard host without pinning it low."""
    for doc in _render(overlay):
        if doc.get("kind") != "Ingress":
            continue
        wildcards = _wildcard_hosts(doc)
        if not wildcards:
            continue

        name = doc["metadata"]["name"]
        annotations = doc["metadata"].get("annotations") or {}

        exact = _exact_hosts(doc)
        assert not exact, (
            f"{overlay}: Ingress {name!r} mixes wildcard {wildcards} with exact "
            f"hosts {exact}. The router.priority annotation is per-OBJECT, so the "
            f"wildcard cannot be de-prioritised without dragging the exact hosts "
            f"down with it. Split the wildcard into its own Ingress."
        )

        assert PRIORITY_ANNOTATION in annotations, (
            f"{overlay}: Ingress {name!r} declares wildcard host(s) {wildcards} "
            f"but pins no {PRIORITY_ANNOTATION}. On Traefik it will outrank every "
            f"exact host in the zone — including other products' hosts. "
            f"Set it to 1."
        )

        priority = int(annotations[PRIORITY_ANNOTATION])
        assert 0 < priority <= MAX_WILDCARD_PRIORITY, (
            f"{overlay}: Ingress {name!r} sets {PRIORITY_ANNOTATION}={priority}. "
            f"A wildcard must sit below every exact-host router "
            f"(1..{MAX_WILDCARD_PRIORITY}). Note Traefik treats 0 as UNSET and "
            f"falls back to rule-length sorting, so 0 does not mean 'lowest'."
        )


class TestMaterializedCustomHostnameRoutes:
    """The runtime-created per-domain Ingresses must pin an explicit high priority."""

    @pytest.fixture
    def profile(self):
        pytest.importorskip("httpx", reason="custom-hostname-api deps not installed")
        from app.config import RouteProfile

        return RouteProfile(
            name="fuzefront",
            namespace="fuzefront",
            service="fuzefront-frontend",
            port=80,
            token="t",
        )

    def test_exact_custom_domain_pins_a_high_priority(self, profile):
        from app.routing import ROUTER_PRIORITY, build_ingress

        manifest = build_ingress("app.corpabc.com", profile)
        annotations = manifest["metadata"]["annotations"]

        assert annotations[PRIORITY_ANNOTATION] == str(ROUTER_PRIORITY)
        # Comfortably above any length-derived priority, so no wildcard anywhere
        # in the cluster can shadow a paying customer's domain.
        assert ROUTER_PRIORITY > 100

    def test_materialized_routes_are_never_wildcards(self, profile):
        """Wildcard custom hostnames are refused upstream; assert it structurally."""
        from app.routing import build_ingress

        manifest = build_ingress("app.corpabc.com", profile)
        assert _wildcard_hosts(manifest) == []
