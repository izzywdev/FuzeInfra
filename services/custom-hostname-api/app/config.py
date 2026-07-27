"""Configuration for the custom hostname API.

Everything is environment-driven so the Helm chart is the single source of
truth and nothing consumer-specific is compiled into the image. Secrets
(Cloudflare token, consumer bearer tokens) arrive via `secretKeyRef` from
SealedSecrets and are never logged.

Route profiles — the mapping from a consumer's bearer token to the workload it
is allowed to route domains at — are supplied as a YAML/JSON document in
`ROUTE_PROFILES` (mounted from a ConfigMap) plus one token env var per profile.
This keeps the mechanism generic: FuzeInfra ships the machinery, the overlay
supplies the data.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from functools import lru_cache

import yaml


@dataclass(frozen=True)
class RouteProfile:
    """One consumer's routing target.

    A bearer token maps to exactly one profile; the profile pins the namespace,
    Service, and paths that a materialized Ingress is allowed to point at. A
    consumer therefore cannot route a customer domain at another consumer's
    workload even if it wanted to.
    """

    name: str
    namespace: str
    service: str
    port: int
    paths: tuple[str, ...] = ("/",)
    ingress_class: str = "traefik"
    #: SHA-256 of the bearer token, resolved at load time from the env var
    #: named by `token_env`. Never the token itself.
    token: str = field(default="", repr=False)

    @property
    def has_token(self) -> bool:
        return bool(self.token)


@dataclass(frozen=True)
class Settings:
    # --- provider -----------------------------------------------------------
    #: "cloudflare" (prod/AWS) or "stub" (kind/local, no Cloudflare account).
    provider: str = "stub"
    cloudflare_api_token: str = field(default="", repr=False)
    cloudflare_zone_id: str = ""
    cloudflare_api_base: str = "https://api.cloudflare.com/client/v4"
    #: Zone the custom hostnames hang off; also used to reject self-referential
    #: domains that the static wildcard already serves.
    managed_zone: str = "fuzefront.com"
    #: Extra zones this platform already serves via wildcard DNS. Domains inside
    #: any of them are rejected with 422 — they need no per-domain provisioning.
    reserved_zones: tuple[str, ...] = ()

    #: Hostname customers CNAME their domain to. Must be a proxied record in the
    #: managed zone that resolves to the tunnel.
    cname_target: str = "connect.fuzefront.com"

    #: Soft cap so a runaway consumer cannot walk into Cloudflare overage
    #: billing. 0 disables the check. Cloudflare includes 100 custom hostnames
    #: on Free/Pro/Business; beyond that it is $0.10/hostname/month.
    max_custom_hostnames: int = 100

    # --- routing (Kubernetes Ingress materialization) -----------------------
    #: When false the service provisions Cloudflare only and skips the k8s
    #: Ingress write — useful for a laptop with no cluster.
    routing_enabled: bool = True
    kube_api: str = "https://kubernetes.default.svc"
    kube_token_path: str = "/var/run/secrets/kubernetes.io/serviceaccount/token"
    kube_ca_path: str = "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"

    # --- misc ---------------------------------------------------------------
    request_timeout_seconds: float = 15.0
    #: Stub only: seconds before a pending hostname auto-advances to active, so
    #: local EPIC-16 development can exercise the full state machine.
    stub_activate_after_seconds: float = 20.0
    stub_state_path: str = "/tmp/custom-hostnames-stub.json"

    profiles: tuple[RouteProfile, ...] = ()

    def profile(self, name: str) -> RouteProfile | None:
        return next((p for p in self.profiles if p.name == name), None)

    def profile_for_token(self, token: str) -> RouteProfile | None:
        """Constant-time-ish lookup of the profile a bearer token grants."""
        import hmac

        for p in self.profiles:
            if p.token and hmac.compare_digest(p.token, token):
                return p
        return None


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _load_profiles() -> tuple[RouteProfile, ...]:
    """Parse ROUTE_PROFILES (YAML or JSON) and bind each profile's token.

    Each entry:
        - name: fuzefront
          namespace: fuzefront
          service: fuzefront-frontend
          port: 80
          paths: ["/", "/api", "/socket.io"]
          tokenEnv: CONSUMER_TOKEN_FUZEFRONT

    A profile whose token env var is absent or empty is loaded but unusable —
    it can never match a request, so a half-configured overlay fails closed.
    """
    raw = os.getenv("ROUTE_PROFILES", "").strip()
    if not raw:
        path = os.getenv("ROUTE_PROFILES_FILE", "").strip()
        if path and os.path.exists(path):
            with open(path, encoding="utf-8") as fh:
                raw = fh.read()
    if not raw:
        return ()

    try:
        parsed = yaml.safe_load(raw)  # YAML is a superset of JSON
    except yaml.YAMLError as exc:  # pragma: no cover - config typo path
        raise RuntimeError(f"ROUTE_PROFILES is not valid YAML/JSON: {exc}") from exc

    if isinstance(parsed, dict):
        parsed = parsed.get("profiles", [])
    if not isinstance(parsed, list):
        raise RuntimeError("ROUTE_PROFILES must be a list of profile objects.")

    profiles: list[RouteProfile] = []
    for entry in parsed:
        if not isinstance(entry, dict) or "name" not in entry:
            raise RuntimeError(f"Invalid route profile entry: {entry!r}")
        token_env = entry.get("tokenEnv") or f"CONSUMER_TOKEN_{str(entry['name']).upper().replace('-', '_')}"
        paths = entry.get("paths") or ["/"]
        profiles.append(
            RouteProfile(
                name=str(entry["name"]),
                namespace=str(entry.get("namespace", "")),
                service=str(entry.get("service", "")),
                port=int(entry.get("port", 80)),
                paths=tuple(str(p) for p in paths),
                ingress_class=str(entry.get("ingressClass", "traefik")),
                token=os.getenv(token_env, "").strip(),
            )
        )
    return tuple(profiles)


def _csv(name: str) -> tuple[str, ...]:
    raw = os.getenv(name, "").strip()
    if not raw:
        return ()
    return tuple(part.strip().lower() for part in raw.split(",") if part.strip())


def load_settings() -> Settings:
    provider = os.getenv("PROVIDER", "stub").strip().lower()
    if provider not in {"cloudflare", "stub"}:
        raise RuntimeError(f"PROVIDER must be 'cloudflare' or 'stub', got {provider!r}")

    settings = Settings(
        provider=provider,
        cloudflare_api_token=os.getenv("CLOUDFLARE_API_TOKEN", "").strip(),
        cloudflare_zone_id=os.getenv("CLOUDFLARE_ZONE_ID", "").strip(),
        cloudflare_api_base=os.getenv("CLOUDFLARE_API_BASE", Settings.cloudflare_api_base).rstrip("/"),
        managed_zone=os.getenv("MANAGED_ZONE", Settings.managed_zone).strip().lower(),
        reserved_zones=_csv("RESERVED_ZONES"),
        cname_target=os.getenv("CNAME_TARGET", Settings.cname_target).strip(),
        max_custom_hostnames=int(os.getenv("MAX_CUSTOM_HOSTNAMES", Settings.max_custom_hostnames)),
        routing_enabled=_env_bool("ROUTING_ENABLED", Settings.routing_enabled),
        kube_api=os.getenv("KUBERNETES_API", Settings.kube_api).rstrip("/"),
        request_timeout_seconds=float(os.getenv("REQUEST_TIMEOUT_SECONDS", Settings.request_timeout_seconds)),
        stub_activate_after_seconds=float(
            os.getenv("STUB_ACTIVATE_AFTER_SECONDS", Settings.stub_activate_after_seconds)
        ),
        stub_state_path=os.getenv("STUB_STATE_PATH", Settings.stub_state_path),
        profiles=_load_profiles(),
    )

    if settings.provider == "cloudflare":
        missing = [
            name
            for name, value in (
                ("CLOUDFLARE_API_TOKEN", settings.cloudflare_api_token),
                ("CLOUDFLARE_ZONE_ID", settings.cloudflare_zone_id),
            )
            if not value
        ]
        if missing:
            raise RuntimeError(f"PROVIDER=cloudflare requires: {', '.join(missing)}")

    return settings


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return load_settings()


def describe(settings: Settings) -> str:
    """Startup log line. Deliberately contains no secret material."""
    return json.dumps(
        {
            "provider": settings.provider,
            "managed_zone": settings.managed_zone,
            "cname_target": settings.cname_target,
            "routing_enabled": settings.routing_enabled,
            "max_custom_hostnames": settings.max_custom_hostnames,
            "profiles": [
                {
                    "name": p.name,
                    "namespace": p.namespace,
                    "service": p.service,
                    "port": p.port,
                    "token_configured": p.has_token,
                }
                for p in settings.profiles
            ],
        },
        sort_keys=True,
    )
