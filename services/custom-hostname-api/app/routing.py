"""Materializes the in-cluster routing for a custom hostname.

Why this exists
---------------
Cloudflare for SaaS solves DNS, ownership, and TLS at the edge — but it does
not teach the in-cluster ingress controller about the new host. Once the edge
is happy, the request still arrives at `traefik.kube-system:80` carrying
`Host: app.corpabc.com`, and Traefik host-routes strictly by Ingress rule. With
no matching rule the customer gets a 404 from Traefik, not the portal. So each
custom hostname needs exactly one small `Ingress` object.

Why a runtime write does not violate GitOps
-------------------------------------------
Prod is Argo CD with `automated: {prune: true, selfHeal: true}`, which is why
FuzeInfra never hand-edits live resources. Argo prunes only resources it
*tracks* — objects carrying its tracking label/annotation that have vanished
from the desired state. The Ingresses created here are deliberately written
WITHOUT any Argo tracking metadata and live in the consumer's namespace, so
they are invisible to the FuzeInfra Application's reconcile loop: selfHeal will
not revert them and prune will not delete them.

The alternative — a host-less catch-all Ingress — was rejected: it would make
FuzeFront the default backend for every unrouted host in the shared cluster,
turning Traefik's "404 for unconfigured hosts" safety property into a silent
mis-route for every other product on the platform.
"""

from __future__ import annotations

import hashlib
import os
import re
from typing import Any

import httpx

from .config import RouteProfile, Settings
from .errors import upstream_error

#: Objects we create carry these so they can be listed, audited, and garbage
#: collected — and so a human reading the cluster knows what wrote them.
MANAGED_BY = "fuzeinfra-custom-hostname-api"

#: Explicit Traefik router priority for a materialized custom-hostname route.
#:
#: Traefik sorts routers by RULE LENGTH by default, not by host specificity, so a
#: wildcard host rule can outrank an exact one purely because its compiled rule
#: string is longer. That is not theoretical — a `*.fuzefront.com` Ingress rule
#: captured another product's exact `plan.fuzefront.com` route in production
#: (FuzeFront#431). A paying customer's custom domain must never be collateral in
#: that kind of accident, so we pin the priority instead of inheriting whatever
#: the length arithmetic happens to produce on the current Traefik version.
#:
#: 1000 sits far above any length-derived priority (an exact host rule computes to
#: roughly 26-60) and far below Traefik's ceiling. The annotation is inert on
#: ingress-nginx, so the local overlay is unaffected.
#: See docs/consuming-repos/CUSTOM_DOMAINS.md §3.
ROUTER_PRIORITY = 1000

_UNSAFE = re.compile(r"[^a-z0-9-]")


def ingress_name(domain: str) -> str:
    """Deterministic, RFC 1123-safe Ingress name for a domain.

    Deterministic matters: DELETE has to be able to find the object that CREATE
    made, without consulting any state the service does not own.
    """
    slug = _UNSAFE.sub("-", domain.lower()).strip("-")
    digest = hashlib.sha256(domain.lower().encode()).hexdigest()[:8]
    if len(slug) > 40:
        slug = slug[:40].strip("-")
    return f"custom-domain-{slug}-{digest}"


def build_ingress(domain: str, profile: RouteProfile) -> dict[str, Any]:
    """The Ingress object routing `domain` to the profile's Service.

    No TLS block: Cloudflare terminates edge TLS and the tunnel delivers plain
    HTTP to Traefik, exactly as every other FuzeInfra Ingress does.
    """
    return {
        "apiVersion": "networking.k8s.io/v1",
        "kind": "Ingress",
        "metadata": {
            "name": ingress_name(domain),
            "namespace": profile.namespace,
            "labels": {
                "app.kubernetes.io/managed-by": MANAGED_BY,
                "fuzeinfra.io/custom-hostname-profile": profile.name,
            },
            "annotations": {
                # Deterministic routing: never inherit Traefik's rule-length
                # default, which lets a wildcard elsewhere in the cluster outrank
                # this exact host. See ROUTER_PRIORITY.
                "traefik.ingress.kubernetes.io/router.priority": str(ROUTER_PRIORITY),
                # The domain in full: the label value above is truncated/sanitized,
                # this is the authoritative record of what the object is for.
                "fuzeinfra.io/custom-hostname": domain,
                "fuzeinfra.io/managed-note": (
                    "Created at runtime by the FuzeInfra custom hostname API. "
                    "Intentionally carries no Argo CD tracking metadata so "
                    "selfHeal/prune leave it alone. Do not adopt into a chart."
                ),
            },
        },
        "spec": {
            "ingressClassName": profile.ingress_class,
            "rules": [
                {
                    "host": domain,
                    "http": {
                        "paths": [
                            {
                                "path": path,
                                "pathType": "Prefix",
                                "backend": {
                                    "service": {
                                        "name": profile.service,
                                        "port": {"number": profile.port},
                                    }
                                },
                            }
                            for path in profile.paths
                        ]
                    },
                }
            ],
        },
    }


class IngressRouter:
    """Thin Kubernetes client for the one object kind this service manages.

    Raw HTTP rather than the `kubernetes` package: one resource kind, three
    verbs, and a ServiceAccount token — the full client would be a large
    dependency for a request builder.
    """

    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None):
        self._settings = settings
        self._enabled = settings.routing_enabled
        self._client = client
        if client is None and self._enabled:
            token = ""
            if os.path.exists(settings.kube_token_path):
                with open(settings.kube_token_path, encoding="utf-8") as fh:
                    token = fh.read().strip()
            verify: Any = settings.kube_ca_path if os.path.exists(settings.kube_ca_path) else True
            self._client = httpx.AsyncClient(
                base_url=settings.kube_api,
                timeout=settings.request_timeout_seconds,
                verify=verify,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
            )

    @property
    def enabled(self) -> bool:
        return self._enabled

    def _path(self, namespace: str, name: str = "") -> str:
        base = f"/apis/networking.k8s.io/v1/namespaces/{namespace}/ingresses"
        return f"{base}/{name}" if name else base

    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        assert self._client is not None
        try:
            return await self._client.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise upstream_error("Kubernetes API is unreachable.", str(exc)) from exc

    async def ensure(self, domain: str, profile: RouteProfile) -> bool:
        """Create-or-replace the routing Ingress. Returns readiness.

        Idempotent: a 409 from CREATE means the object exists, so we PUT the
        desired state over it. That also repairs an Ingress a human edited by
        hand, which keeps "what the API says" and "what the cluster does" in
        agreement.
        """
        if not self._enabled:
            return False

        manifest = build_ingress(domain, profile)
        name = manifest["metadata"]["name"]

        response = await self._request(
            "POST", self._path(profile.namespace), json=manifest
        )
        if response.status_code in (200, 201):
            return True
        if response.status_code != 409:
            raise upstream_error(
                "Could not create the routing Ingress.",
                f"HTTP {response.status_code}: {response.text[:400]}",
            )

        # Already there — read it for the resourceVersion, then replace.
        current = await self._request("GET", self._path(profile.namespace, name))
        if current.status_code != 200:
            raise upstream_error(
                "Routing Ingress exists but could not be read.",
                f"HTTP {current.status_code}: {current.text[:400]}",
            )
        manifest["metadata"]["resourceVersion"] = (
            current.json().get("metadata", {}).get("resourceVersion")
        )
        replaced = await self._request(
            "PUT", self._path(profile.namespace, name), json=manifest
        )
        if replaced.status_code not in (200, 201):
            raise upstream_error(
                "Could not update the routing Ingress.",
                f"HTTP {replaced.status_code}: {replaced.text[:400]}",
            )
        return True

    async def exists(self, domain: str, profile: RouteProfile) -> bool:
        if not self._enabled:
            return False
        response = await self._request(
            "GET", self._path(profile.namespace, ingress_name(domain))
        )
        return response.status_code == 200

    async def remove(self, domain: str, profile: RouteProfile) -> None:
        if not self._enabled:
            return
        response = await self._request(
            "DELETE", self._path(profile.namespace, ingress_name(domain))
        )
        if response.status_code not in (200, 202, 404):
            raise upstream_error(
                "Could not delete the routing Ingress.",
                f"HTTP {response.status_code}: {response.text[:400]}",
            )

    async def health(self) -> tuple[bool, str | None]:
        if not self._enabled:
            return True, "routing disabled — Cloudflare-only mode"
        try:
            response = await self._request("GET", "/apis/networking.k8s.io/v1")
        except Exception as exc:  # noqa: BLE001 - readiness must never raise
            return False, str(getattr(exc, "detail", exc))
        if response.status_code != 200:
            return False, f"Kubernetes API returned HTTP {response.status_code}"
        return True, None

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
