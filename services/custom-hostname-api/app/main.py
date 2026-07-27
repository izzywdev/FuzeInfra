"""FuzeInfra Custom Hostname API.

Cluster-internal service that attaches arbitrary customer domains to a consumer
workload at runtime — no Helm release per domain. See ``openapi.yaml`` for the
frozen contract and ``docs/consuming-repos/CUSTOM_DOMAINS.md`` for the design.

The service has no Ingress and is never routed through the Cloudflare tunnel.
It is reachable only at its in-cluster Service DNS, and only by callers holding
a consumer bearer token.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Query, Request, Response
from fastapi.responses import JSONResponse

from .auth import caller_profile, resolve_profile
from .config import RouteProfile, Settings, describe, get_settings
from .errors import ApiError
from .models import (
    CreateCustomHostnameRequest,
    CustomHostname,
    CustomHostnameList,
    Health,
    ProviderName,
)
from .providers.base import HostnameProvider
from .providers.cloudflare import CloudflareProvider
from .providers.stub import StubProvider
from .routing import IngressRouter
from .service import HostnameService

logger = logging.getLogger("custom-hostname-api")

API_VERSION = "1.0.0"


def build_provider(settings: Settings) -> HostnameProvider:
    if settings.provider == "cloudflare":
        return CloudflareProvider(settings)
    return StubProvider(settings)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    logger.info("starting custom-hostname-api config=%s", describe(settings))
    if not any(p.has_token for p in settings.profiles):
        # Fail loud but stay up: readiness will report degraded, which surfaces
        # the misconfiguration in the deployment rather than in a 401 storm.
        logger.warning(
            "no route profile has a usable token — every request will 401 "
            "until the consumer SealedSecret is present"
        )

    provider = build_provider(settings)
    router = IngressRouter(settings)
    app.state.settings = settings
    app.state.provider = provider
    app.state.router = router
    app.state.service = HostnameService(settings, provider, router)
    try:
        yield
    finally:
        await provider.aclose()
        await router.aclose()


app = FastAPI(
    title="FuzeInfra Custom Hostname API",
    version=API_VERSION,
    lifespan=lifespan,
    # The contract in openapi.yaml is the published artifact; the generated one
    # is for interactive debugging only.
    openapi_url="/openapi.json",
)


@app.exception_handler(ApiError)
async def _api_error_handler(_: Request, exc: ApiError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content=exc.detail)


def _service(request: Request) -> HostnameService:
    return request.app.state.service


# ---------------------------------------------------------------------------
# custom-hostnames
# ---------------------------------------------------------------------------


@app.post(
    "/custom-hostnames",
    response_model=CustomHostname,
    status_code=201,
    tags=["custom-hostnames"],
    operation_id="createCustomHostname",
    summary="Begin validation + certificate issuance for a customer domain",
)
async def create_custom_hostname(
    body: CreateCustomHostnameRequest,
    response: Response,
    request: Request,
    granted: RouteProfile = Depends(caller_profile),
) -> CustomHostname:
    profile = resolve_profile(body.profile, granted)
    result, created = await _service(request).create(body.domain, profile)
    if not created:
        response.status_code = 200
    logger.info(
        "custom hostname %s domain=%s profile=%s tls=%s dns=%s",
        "created" if created else "already existed",
        result.domain,
        profile.name,
        result.tls_status.value,
        result.dns_status.value,
    )
    return result


@app.get(
    "/custom-hostnames",
    response_model=CustomHostnameList,
    tags=["custom-hostnames"],
    operation_id="listCustomHostnames",
    summary="List the domains registered by the calling consumer",
)
async def list_custom_hostnames(
    request: Request,
    limit: int = Query(default=50, ge=1, le=200),
    cursor: str | None = Query(default=None),
    granted: RouteProfile = Depends(caller_profile),
) -> CustomHostnameList:
    # `cursor` is accepted and reserved: the upstream page size (50) already
    # exceeds the default limit, and paging is only reachable once a consumer
    # passes ~200 domains. Returning null keeps clients from looping.
    items = await _service(request).list(granted, limit)
    return CustomHostnameList(items=items, next_cursor=None)


@app.get(
    "/custom-hostnames/{domain}",
    response_model=CustomHostname,
    tags=["custom-hostnames"],
    operation_id="getCustomHostname",
    summary="Poll validation, certificate, and routing status",
)
async def get_custom_hostname(
    domain: str,
    request: Request,
    granted: RouteProfile = Depends(caller_profile),
) -> CustomHostname:
    return await _service(request).get(domain.strip().rstrip(".").lower(), granted)


@app.delete(
    "/custom-hostnames/{domain}",
    status_code=204,
    tags=["custom-hostnames"],
    operation_id="deleteCustomHostname",
    summary="Deprovision a customer domain",
)
async def delete_custom_hostname(
    domain: str,
    request: Request,
    granted: RouteProfile = Depends(caller_profile),
) -> Response:
    normalized = domain.strip().rstrip(".").lower()
    await _service(request).delete(normalized, granted)
    logger.info("custom hostname deleted domain=%s profile=%s", normalized, granted.name)
    return Response(status_code=204)


# ---------------------------------------------------------------------------
# health
# ---------------------------------------------------------------------------


@app.get("/healthz", response_model=Health, tags=["health"], operation_id="healthz")
async def healthz(request: Request) -> Health:
    settings: Settings = request.app.state.settings
    return Health(status="ok", provider=ProviderName(_provider_name(settings)))


@app.get("/readyz", response_model=Health, tags=["health"], operation_id="readyz")
async def readyz(request: Request, response: Response) -> Health:
    settings: Settings = request.app.state.settings
    provider: HostnameProvider = request.app.state.provider
    router: IngressRouter = request.app.state.router

    problems: list[str] = []
    for ready, detail in (await provider.health(), await router.health()):
        if not ready:
            problems.append(detail or "unavailable")
    if not any(p.has_token for p in settings.profiles):
        problems.append("no route profile has a usable consumer token")

    if problems:
        response.status_code = 503
        return Health(
            status="degraded",
            provider=ProviderName(_provider_name(settings)),
            detail="; ".join(problems),
        )
    return Health(status="ok", provider=ProviderName(_provider_name(settings)))


def _provider_name(settings: Settings) -> str:
    return (
        ProviderName.cloudflare_for_saas.value
        if settings.provider == "cloudflare"
        else ProviderName.stub.value
    )
