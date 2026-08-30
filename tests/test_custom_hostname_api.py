"""Unit tests for the FuzeInfra Custom Hostname API.

Everything here runs offline: the Cloudflare provider is exercised against a
mocked transport and the routing layer against a fake Kubernetes API, so the
suite needs neither a cluster nor a Cloudflare account.

The contract test is the important one — it asserts the running app still
matches the frozen `openapi.yaml` that consumers generate their clients from.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

SERVICE_ROOT = Path(__file__).resolve().parents[1] / "services" / "custom-hostname-api"
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))

pytest.importorskip("fastapi", reason="custom-hostname-api tests need fastapi")
pytest.importorskip("httpx", reason="custom-hostname-api tests need httpx")

import httpx  # noqa: E402
import yaml  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import config as config_module  # noqa: E402
from app.config import RouteProfile, Settings  # noqa: E402
from app.models import DnsStatus, TlsStatus  # noqa: E402
from app.providers.cloudflare import CloudflareProvider  # noqa: E402
from app.routing import IngressRouter, build_ingress, ingress_name  # noqa: E402

TOKEN = "test-consumer-token"
OTHER_TOKEN = "other-consumer-token"

PROFILE = RouteProfile(
    name="fuzefront",
    namespace="fuzefront",
    service="fuzefront-frontend",
    port=80,
    paths=("/", "/api", "/socket.io"),
    token=TOKEN,
)
OTHER_PROFILE = RouteProfile(
    name="other",
    namespace="other",
    service="other-frontend",
    port=80,
    token=OTHER_TOKEN,
)


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        provider="stub",
        managed_zone="fuzefront.com",
        reserved_zones=("fuzefront.local",),
        cname_target="connect.fuzefront.com",
        routing_enabled=False,
        stub_activate_after_seconds=0.001,
        stub_state_path=str(tmp_path / "stub.json"),
        profiles=(PROFILE, OTHER_PROFILE),
    )


@pytest.fixture
def client(settings, monkeypatch) -> TestClient:
    monkeypatch.setattr(config_module, "load_settings", lambda: settings)
    config_module.get_settings.cache_clear()
    from app.main import app

    with TestClient(app) as test_client:
        yield test_client
    config_module.get_settings.cache_clear()


def auth(token: str = TOKEN) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# contract
# ---------------------------------------------------------------------------


class TestFrozenContract:
    """openapi.yaml is what consumers generate clients from — drift breaks them."""

    @pytest.fixture
    def frozen(self) -> dict:
        return yaml.safe_load((SERVICE_ROOT / "openapi.yaml").read_text(encoding="utf-8"))

    def test_every_frozen_path_and_method_is_implemented(self, frozen, client):
        generated = client.app.openapi()
        for path, operations in frozen["paths"].items():
            assert path in generated["paths"], f"{path} missing from the app"
            for method in operations:
                if method in {"parameters", "summary", "description"}:
                    continue
                assert method in generated["paths"][path], f"{method.upper()} {path} missing"

    def test_operation_ids_match(self, frozen, client):
        generated = client.app.openapi()
        for path, operations in frozen["paths"].items():
            for method, spec in operations.items():
                if not isinstance(spec, dict) or "operationId" not in spec:
                    continue
                assert generated["paths"][path][method]["operationId"] == spec["operationId"]

    def test_custom_hostname_fields_match(self, frozen, client):
        frozen_props = set(frozen["components"]["schemas"]["CustomHostname"]["properties"])
        generated = client.app.openapi()["components"]["schemas"]["CustomHostname"]
        assert set(generated["properties"]) == frozen_props

    def test_status_enums_match(self, frozen, client):
        generated = client.app.openapi()["components"]["schemas"]
        for name in ("DnsStatus", "TlsStatus"):
            assert set(generated[name]["enum"]) == set(
                frozen["components"]["schemas"][name]["enum"]
            )

    @pytest.mark.parametrize(
        "schema_name", ["CustomHostname", "VerificationRecord", "Verification", "Routing"]
    )
    def test_required_fields_match(self, frozen, client, schema_name):
        """A field the spec calls required must be required in the model too.

        Caught in review by a consumer: `VerificationRecord.purpose` was optional
        in the spec while the guide told callers to render records grouped BY
        purpose. An optional field they cannot rely on is a broken contract.
        """
        generated = client.app.openapi()["components"]["schemas"][schema_name]
        frozen_required = set(frozen["components"]["schemas"][schema_name].get("required", []))
        assert set(generated.get("required", [])) == frozen_required

    def test_every_error_the_service_can_raise_is_declared(self, frozen):
        """Undeclared status codes are untypeable by a generated client.

        Caught in review by a consumer: `429 quota_exceeded` existed in the
        `Error.error` enum and in the prose, but was not declared as a response
        on POST, so a generated client had no type for the one error an operator
        is most likely to hit.
        """
        post = frozen["paths"]["/custom-hostnames"]["post"]["responses"]
        assert "429" in post, (
            "POST can return 429 quota_exceeded (the local cap is checked before "
            "Cloudflare is called) — declare it or a generated client cannot type it"
        )

        # Every code the error module can produce must appear on some operation.
        declared = {
            code
            for path in frozen["paths"].values()
            for method, spec in path.items()
            if isinstance(spec, dict) and "responses" in spec
            for code in spec["responses"]
        }
        for code in ("400", "401", "403", "404", "422", "429", "502"):
            assert code in declared, f"{code} is reachable but declared nowhere"


# ---------------------------------------------------------------------------
# auth
# ---------------------------------------------------------------------------


class TestAuth:
    def test_missing_token_is_401(self, client):
        assert client.post("/custom-hostnames", json={"domain": "a.example.com"}).status_code == 401

    def test_unknown_token_is_401(self, client):
        response = client.post(
            "/custom-hostnames", json={"domain": "a.example.com"}, headers=auth("nope")
        )
        assert response.status_code == 401
        assert response.json()["error"] == "unauthorized"

    def test_non_bearer_scheme_is_401(self, client):
        response = client.post(
            "/custom-hostnames",
            json={"domain": "a.example.com"},
            headers={"Authorization": f"Basic {TOKEN}"},
        )
        assert response.status_code == 401

    def test_requesting_an_ungranted_profile_is_403(self, client):
        response = client.post(
            "/custom-hostnames",
            json={"domain": "a.example.com", "profile": "other"},
            headers=auth(),
        )
        assert response.status_code == 403
        assert response.json()["error"] == "forbidden"

    def test_health_endpoints_are_unauthenticated(self, client):
        assert client.get("/healthz").status_code == 200
        assert client.get("/readyz").status_code in (200, 503)


# ---------------------------------------------------------------------------
# validation
# ---------------------------------------------------------------------------


class TestDomainValidation:
    @pytest.mark.parametrize(
        "domain",
        ["*.example.com", "example", "-bad.example.com", "", "a..b.com"],
    )
    def test_malformed_domains_are_rejected(self, client, domain):
        response = client.post("/custom-hostnames", json={"domain": domain}, headers=auth())
        assert response.status_code == 422

    @pytest.mark.parametrize("domain", ["tenant.fuzefront.com", "fuzefront.com", "x.fuzefront.local"])
    def test_platform_managed_zones_are_rejected(self, client, domain):
        response = client.post("/custom-hostnames", json={"domain": domain}, headers=auth())
        assert response.status_code == 422
        assert response.json()["error"] == "validation_error"

    def test_domain_is_normalized(self, client):
        response = client.post(
            "/custom-hostnames", json={"domain": "APP.CorpABC.com."}, headers=auth()
        )
        assert response.status_code == 201
        assert response.json()["domain"] == "app.corpabc.com"

    def test_unknown_body_field_is_rejected(self, client):
        response = client.post(
            "/custom-hostnames",
            json={"domain": "a.example.com", "namespace": "kube-system"},
            headers=auth(),
        )
        assert response.status_code == 422


# ---------------------------------------------------------------------------
# lifecycle (stub provider)
# ---------------------------------------------------------------------------


class TestLifecycle:
    def test_create_returns_the_records_the_customer_must_publish(self, client):
        response = client.post(
            "/custom-hostnames", json={"domain": "app.corpabc.com"}, headers=auth()
        )
        assert response.status_code == 201
        body = response.json()

        assert body["profile"] == "fuzefront"
        assert body["verification"]["method"] == "txt"
        purposes = {r["purpose"] for r in body["verification"]["records"]}
        assert purposes == {"ownership", "certificate", "routing"}

        routing = next(
            r for r in body["verification"]["records"] if r["purpose"] == "routing"
        )
        assert routing["method"] == "cname"
        assert routing["value"] == "connect.fuzefront.com"
        assert body["routing"]["cname_target"] == "connect.fuzefront.com"

    def test_create_is_idempotent(self, client):
        payload = {"domain": "app.corpabc.com"}
        assert client.post("/custom-hostnames", json=payload, headers=auth()).status_code == 201
        again = client.post("/custom-hostnames", json=payload, headers=auth())
        assert again.status_code == 200
        assert again.json()["domain"] == "app.corpabc.com"

    def test_stub_reaches_active(self, client):
        client.post("/custom-hostnames", json={"domain": "app.corpabc.com"}, headers=auth())
        # The stub advances tls_status on a wall-clock timer, so an immediate
        # read can still be mid-lifecycle on a fast runner. Poll until it settles
        # rather than racing a single read against the activation window.
        deadline = time.monotonic() + 5.0
        body = client.get("/custom-hostnames/app.corpabc.com", headers=auth()).json()
        while body["tls_status"] != "active" and time.monotonic() < deadline:
            body = client.get("/custom-hostnames/app.corpabc.com", headers=auth()).json()
        assert body["tls_status"] == "active"
        assert body["dns_status"] == "active"
        assert body["active"] is True
        assert body["certificate"]["not_after"] is not None

    def test_get_unknown_domain_is_404(self, client):
        response = client.get("/custom-hostnames/nope.example.com", headers=auth())
        assert response.status_code == 404
        assert response.json()["error"] == "not_found"

    def test_delete_then_get(self, client):
        client.post("/custom-hostnames", json={"domain": "app.corpabc.com"}, headers=auth())
        assert client.delete("/custom-hostnames/app.corpabc.com", headers=auth()).status_code == 204
        assert client.get("/custom-hostnames/app.corpabc.com", headers=auth()).status_code == 404

    def test_delete_is_idempotent(self, client):
        assert client.delete("/custom-hostnames/never.example.com", headers=auth()).status_code == 204

    def test_list_is_scoped_and_limited(self, client):
        for i in range(3):
            client.post(
                "/custom-hostnames", json={"domain": f"app{i}.corpabc.com"}, headers=auth()
            )
        body = client.get("/custom-hostnames?limit=2", headers=auth()).json()
        assert len(body["items"]) == 2
        assert body["next_cursor"] is None


# ---------------------------------------------------------------------------
# Cloudflare provider mapping
# ---------------------------------------------------------------------------


def _cf_settings() -> Settings:
    return Settings(
        provider="cloudflare",
        cloudflare_api_token="token",
        cloudflare_zone_id="zone123",
        cname_target="connect.fuzefront.com",
        max_custom_hostnames=0,
        profiles=(PROFILE,),
    )


def _cf_provider(handler) -> CloudflareProvider:
    settings = _cf_settings()
    client = httpx.AsyncClient(
        base_url=settings.cloudflare_api_base,
        transport=httpx.MockTransport(handler),
    )
    return CloudflareProvider(settings, client=client)


CF_RESULT = {
    "id": "0d89c70d-ad9f-4843-b99f-6cc0252067e9",
    "hostname": "app.corpabc.com",
    "status": "pending",
    "created_at": "2026-07-27T10:00:00.000000Z",
    "ownership_verification": {
        "type": "txt",
        "name": "_cf-custom-hostname.app.corpabc.com",
        "value": "5cc07dfa-0d4d-4bbc-a6f9-8c8a3d5e1f11",
    },
    "ssl": {
        "status": "pending_validation",
        "method": "txt",
        "type": "dv",
        "validation_records": [
            {
                "txt_name": "_acme-challenge.app.corpabc.com",
                "txt_value": "GHi3mDIVQuKLLDXqDBLfMzZbSbNCEwqhoLBLFcHJdA",
            }
        ],
    },
}


class TestCloudflareProvider:
    @pytest.mark.asyncio
    async def test_create_posts_txt_dcv_and_maps_the_response(self):
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "GET":
                return httpx.Response(200, json={"success": True, "result": []})
            import json as _json

            seen["body"] = _json.loads(request.content)
            return httpx.Response(200, json={"success": True, "result": CF_RESULT})

        provider = _cf_provider(handler)
        record, created = await provider.create("app.corpabc.com")

        assert created is True
        # TXT DCV lets the customer validate before cutting DNS over to us.
        assert seen["body"]["ssl"]["method"] == "txt"
        assert seen["body"]["ssl"]["wildcard"] is False
        assert record.dns_status is DnsStatus.pending
        assert record.tls_status is TlsStatus.pending_validation

        records = {r.purpose.value: r for r in record.verification.records}
        assert records["ownership"].record == "_cf-custom-hostname.app.corpabc.com"
        assert records["certificate"].record == "_acme-challenge.app.corpabc.com"
        assert records["routing"].value == "connect.fuzefront.com"
        await provider.aclose()

    @pytest.mark.asyncio
    async def test_existing_hostname_is_not_recreated(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "POST":
                raise AssertionError("must not POST when the hostname already exists")
            return httpx.Response(200, json={"success": True, "result": [CF_RESULT]})

        provider = _cf_provider(handler)
        _, created = await provider.create("app.corpabc.com")
        assert created is False
        await provider.aclose()

    @pytest.mark.asyncio
    async def test_active_certificate_is_mapped(self):
        active = {
            **CF_RESULT,
            "status": "active",
            "ssl": {
                "status": "active",
                "certificates": [
                    {
                        "issuer": "Let's Encrypt",
                        "not_before": "2026-07-27T10:00:00.000000Z",
                        "not_after": "2026-10-25T10:00:00.000000Z",
                        "serial_number": "abc123",
                    }
                ],
            },
        }

        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"success": True, "result": [active]})

        provider = _cf_provider(handler)
        record = await provider.get("app.corpabc.com")
        assert record.dns_status is DnsStatus.active
        assert record.tls_status is TlsStatus.active
        assert record.certificate.serial_number == "abc123"
        await provider.aclose()

    @pytest.mark.asyncio
    async def test_validation_errors_become_failed(self):
        broken = {
            **CF_RESULT,
            "ssl": {
                **CF_RESULT["ssl"],
                "validation_errors": [{"message": "no TXT record found"}],
            },
        }

        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"success": True, "result": [broken]})

        provider = _cf_provider(handler)
        record = await provider.get("app.corpabc.com")
        assert record.tls_status is TlsStatus.failed
        assert "no TXT record found" in record.error
        await provider.aclose()

    @pytest.mark.asyncio
    async def test_unknown_upstream_status_does_not_read_as_failure(self):
        future = {**CF_RESULT, "status": "some_new_state", "ssl": {"status": "some_new_ssl_state"}}

        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"success": True, "result": [future]})

        provider = _cf_provider(handler)
        record = await provider.get("app.corpabc.com")
        assert record.dns_status is DnsStatus.pending
        assert record.tls_status is TlsStatus.pending_validation
        await provider.aclose()

    @pytest.mark.asyncio
    async def test_quota_error_is_surfaced_as_429(self):
        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(
                403,
                json={"success": False, "errors": [{"code": 1414, "message": "quota reached"}]},
            )

        provider = _cf_provider(handler)
        with pytest.raises(Exception) as exc:
            await provider.get("app.corpabc.com")
        assert getattr(exc.value, "status_code", None) == 429
        await provider.aclose()

    @pytest.mark.asyncio
    async def test_local_cap_blocks_before_cloudflare_billing_kicks_in(self):
        settings = Settings(
            provider="cloudflare",
            cloudflare_api_token="token",
            cloudflare_zone_id="zone123",
            max_custom_hostnames=1,
            profiles=(PROFILE,),
        )

        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "POST":
                raise AssertionError("must not POST past the configured cap")
            hostname = request.url.params.get("hostname")
            if hostname:  # the existence probe for the new domain
                return httpx.Response(200, json={"success": True, "result": []})
            return httpx.Response(
                200,
                json={
                    "success": True,
                    "result": [CF_RESULT],
                    "result_info": {"total_pages": 1},
                },
            )

        provider = CloudflareProvider(
            settings,
            client=httpx.AsyncClient(
                base_url=settings.cloudflare_api_base,
                transport=httpx.MockTransport(handler),
            ),
        )
        with pytest.raises(Exception) as exc:
            await provider.create("new.corpabc.com")
        assert getattr(exc.value, "status_code", None) == 429
        await provider.aclose()


# ---------------------------------------------------------------------------
# routing / Ingress materialization
# ---------------------------------------------------------------------------


class TestIngressMaterialization:
    def test_name_is_deterministic_and_rfc1123_safe(self):
        first = ingress_name("app.corpabc.com")
        assert first == ingress_name("app.corpabc.com")
        assert first.replace("-", "").isalnum()
        assert first.islower()
        assert len(first) <= 63

    def test_long_domains_stay_within_the_name_limit(self):
        long_domain = ".".join(["averyverylonglabelindeed"] * 8) + ".com"
        assert len(ingress_name(long_domain)) <= 63

    def test_distinct_domains_never_collide(self):
        a = "x" * 45 + "a.example.com"
        b = "x" * 45 + "b.example.com"
        assert ingress_name(a) != ingress_name(b)

    def test_manifest_routes_the_host_to_the_profile_service(self):
        manifest = build_ingress("app.corpabc.com", PROFILE)
        rule = manifest["spec"]["rules"][0]
        assert rule["host"] == "app.corpabc.com"
        assert manifest["metadata"]["namespace"] == "fuzefront"
        assert manifest["spec"]["ingressClassName"] == "traefik"
        assert [p["path"] for p in rule["http"]["paths"]] == ["/", "/api", "/socket.io"]
        backend = rule["http"]["paths"][0]["backend"]["service"]
        assert backend == {"name": "fuzefront-frontend", "port": {"number": 80}}

    def test_manifest_carries_no_argo_tracking_metadata(self):
        """selfHeal/prune must never adopt or delete a runtime-created Ingress."""
        manifest = build_ingress("app.corpabc.com", PROFILE)
        meta = manifest["metadata"]
        serialized = str(meta)
        assert "argocd" not in serialized
        assert "app.kubernetes.io/instance" not in meta["labels"]
        assert meta["labels"]["app.kubernetes.io/managed-by"] == "fuzeinfra-custom-hostname-api"
        assert meta["annotations"]["fuzeinfra.io/custom-hostname"] == "app.corpabc.com"

    def test_manifest_declares_no_tls_block(self):
        """Cloudflare terminates edge TLS; Traefik serves plain HTTP."""
        assert "tls" not in build_ingress("app.corpabc.com", PROFILE)["spec"]

    @pytest.mark.asyncio
    async def test_ensure_replaces_an_existing_ingress(self):
        calls: list[tuple[str, str]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append((request.method, request.url.path))
            if request.method == "POST":
                return httpx.Response(409, json={"reason": "AlreadyExists"})
            if request.method == "GET":
                return httpx.Response(200, json={"metadata": {"resourceVersion": "42"}})
            return httpx.Response(200, json={})

        router = IngressRouter(
            Settings(routing_enabled=True),
            client=httpx.AsyncClient(
                base_url="https://kubernetes.default.svc",
                transport=httpx.MockTransport(handler),
            ),
        )
        assert await router.ensure("app.corpabc.com", PROFILE) is True
        assert [method for method, _ in calls] == ["POST", "GET", "PUT"]
        await router.aclose()

    @pytest.mark.asyncio
    async def test_remove_tolerates_a_missing_ingress(self):
        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(404, json={"reason": "NotFound"})

        router = IngressRouter(
            Settings(routing_enabled=True),
            client=httpx.AsyncClient(
                base_url="https://kubernetes.default.svc",
                transport=httpx.MockTransport(handler),
            ),
        )
        await router.remove("app.corpabc.com", PROFILE)  # must not raise
        await router.aclose()


# ---------------------------------------------------------------------------
# configuration
# ---------------------------------------------------------------------------


class TestConfig:
    def test_cloudflare_provider_requires_credentials(self, monkeypatch):
        monkeypatch.setenv("PROVIDER", "cloudflare")
        monkeypatch.delenv("CLOUDFLARE_API_TOKEN", raising=False)
        monkeypatch.delenv("CLOUDFLARE_ZONE_ID", raising=False)
        monkeypatch.setenv("ROUTE_PROFILES", "[]")
        with pytest.raises(RuntimeError, match="CLOUDFLARE_API_TOKEN"):
            config_module.load_settings()

    def test_profiles_bind_tokens_from_the_environment(self, monkeypatch):
        monkeypatch.setenv("PROVIDER", "stub")
        monkeypatch.setenv(
            "ROUTE_PROFILES",
            "- name: fuzefront\n"
            "  namespace: fuzefront\n"
            "  service: fuzefront-frontend\n"
            "  port: 80\n"
            "  tokenEnv: CONSUMER_TOKEN_FUZEFRONT\n",
        )
        monkeypatch.setenv("CONSUMER_TOKEN_FUZEFRONT", "s3cret")
        settings = config_module.load_settings()
        assert settings.profile_for_token("s3cret").name == "fuzefront"
        assert settings.profile_for_token("wrong") is None

    def test_profile_without_a_token_can_never_authenticate(self, monkeypatch):
        monkeypatch.setenv("PROVIDER", "stub")
        monkeypatch.setenv("ROUTE_PROFILES", "- name: fuzefront\n  namespace: fuzefront\n")
        monkeypatch.delenv("CONSUMER_TOKEN_FUZEFRONT", raising=False)
        settings = config_module.load_settings()
        assert settings.profiles[0].has_token is False
        assert settings.profile_for_token("") is None

    def test_describe_never_leaks_secrets(self, monkeypatch):
        monkeypatch.setenv("PROVIDER", "stub")
        monkeypatch.setenv("ROUTE_PROFILES", "- name: fuzefront\n  tokenEnv: TOK\n")
        monkeypatch.setenv("TOK", "super-secret-value")
        rendered = config_module.describe(config_module.load_settings())
        assert "super-secret-value" not in rendered
        assert '"token_configured": true' in rendered
