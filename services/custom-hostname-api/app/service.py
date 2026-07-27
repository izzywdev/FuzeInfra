"""Composition layer: provider state + in-cluster routing -> API resource.

Neither half alone makes a customer domain work. Cloudflare can hold a valid
certificate for `app.corpabc.com` while Traefik still 404s it, and an Ingress
can be perfectly correct while the customer has not published their TXT records
yet. `active` is true only when both halves agree, so a consumer has exactly one
field to gate on.
"""

from __future__ import annotations

from datetime import datetime, timezone

from .config import RouteProfile, Settings
from .errors import forbidden, not_found, validation_error
from .models import (
    CustomHostname,
    DnsStatus,
    Routing,
    TlsStatus,
)
from .providers.base import HostnameProvider, ProviderRecord
from .routing import IngressRouter, ingress_name


class HostnameService:
    def __init__(
        self,
        settings: Settings,
        provider: HostnameProvider,
        router: IngressRouter,
    ):
        self._settings = settings
        self._provider = provider
        self._router = router

    # -- validation ----------------------------------------------------------

    def validate_domain(self, domain: str) -> str:
        """Reject domains this mechanism must not be used for.

        Hosts inside a zone we already serve by wildcard need no per-domain
        provisioning — routing them here would create a redundant Cloudflare
        custom hostname that quietly consumes quota and can shadow the wildcard.
        """
        managed = [self._settings.managed_zone, *self._settings.reserved_zones]
        for zone in managed:
            if zone and (domain == zone or domain.endswith(f".{zone}")):
                raise validation_error(
                    f"{domain} is inside the platform-managed zone {zone}.",
                    "Hosts in this zone are already served by the static wildcard "
                    "DNS + certificate; no custom hostname is required.",
                )
        return domain

    # -- assembly ------------------------------------------------------------

    def _compose(
        self,
        record: ProviderRecord,
        profile: RouteProfile,
        ingress_ready: bool,
    ) -> CustomHostname:
        active = (
            record.dns_status is DnsStatus.active
            and record.tls_status is TlsStatus.active
            and (ingress_ready or not self._router.enabled)
        )
        return CustomHostname(
            domain=record.domain,
            profile=profile.name,
            active=active,
            dns_status=record.dns_status,
            tls_status=record.tls_status,
            verification=record.verification,
            routing=Routing(
                cname_target=self._settings.cname_target,
                ingress_ready=ingress_ready,
                ingress_name=ingress_name(record.domain) if self._router.enabled else None,
            ),
            certificate=record.certificate,
            error=record.error,
            provider=record.provider,
            created_at=record.created_at or datetime.now(timezone.utc),
            updated_at=record.updated_at,
        )

    # -- operations ----------------------------------------------------------

    async def create(
        self, domain: str, profile: RouteProfile
    ) -> tuple[CustomHostname, bool]:
        self.validate_domain(domain)

        # Guard against one consumer hijacking a domain another already owns.
        # Checked before the provider call so a cross-profile POST never so much
        # as touches Cloudflare.
        owner = await self._owner_of(domain)
        if owner is not None and owner != profile.name:
            raise forbidden(
                f"{domain} is already attached to a different route profile.",
                "Deprovision it from its current owner first.",
            )

        record, created = await self._provider.create(domain)

        # Cloudflare first, routing second: a failure here leaves a visible
        # ingress_ready=false rather than a half-deleted edge registration, and
        # POST is idempotent so the consumer just retries.
        ingress_ready = await self._router.ensure(domain, profile)

        return self._compose(record, profile, ingress_ready), created

    async def get(self, domain: str, profile: RouteProfile) -> CustomHostname:
        owner = await self._owner_of(domain)
        if owner is not None and owner != profile.name:
            raise forbidden(f"{domain} belongs to a different route profile.")

        record = await self._provider.get(domain)
        if record is None:
            raise not_found(f"{domain} is not provisioned.")

        ingress_ready = await self._router.exists(domain, profile)
        return self._compose(record, profile, ingress_ready)

    async def delete(self, domain: str, profile: RouteProfile) -> None:
        owner = await self._owner_of(domain)
        if owner is not None and owner != profile.name:
            raise forbidden(f"{domain} belongs to a different route profile.")

        # Routing first: stop serving the host before the edge registration goes
        # away, so there is no window where Cloudflare still sends traffic at a
        # rule we have already decided to retire.
        await self._router.remove(domain, profile)
        await self._provider.delete(domain)

    async def list(self, profile: RouteProfile, limit: int) -> list[CustomHostname]:
        records = await self._provider.list()
        owned = []
        for record in records:
            owner = await self._owner_of(record.domain)
            if owner is not None and owner != profile.name:
                continue
            ingress_ready = await self._router.exists(record.domain, profile)
            owned.append(self._compose(record, profile, ingress_ready))
            if len(owned) >= limit:
                break
        return owned

    # -- ownership -----------------------------------------------------------

    async def _owner_of(self, domain: str) -> str | None:
        """Which profile a domain is attached to, or None if unattached.

        Ownership is recorded by the routing Ingress itself, so the cluster is
        the source of truth and this service needs no database. When routing is
        disabled (Cloudflare-only mode) there is nowhere to record ownership; a
        single-profile deployment is then unambiguous, and a multi-profile one
        cannot safely answer, so it reports "unknown" and the caller's own
        profile applies.
        """
        if not self._router.enabled:
            return None
        for candidate in self._settings.profiles:
            if await self._router.exists(domain, candidate):
                return candidate.name
        return None
